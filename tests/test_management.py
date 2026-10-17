import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values, set_key
from werkzeug.security import check_password_hash, generate_password_hash
from web import management, runtime
from web.app import create_app


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target in (management, runtime):
            mock = patch.object(target, 'ROOT', self.root); mock.start(); self.addCleanup(mock.stop)
        env = patch.dict(os.environ, {'BYPAY_PORT':'18888'}, clear=True); env.start(); self.addCleanup(env.stop)
        hosts = patch('web.runtime.local_hosts',return_value=['localhost','127.0.0.1']); hosts.start(); self.addCleanup(hosts.stop)

    def prepare(self):
        with patch('web.management.host_address',return_value='127.0.0.1'),patch('builtins.print'): management.prepare()
        state,source,values=management.configuration()
        return state,source,values

    def test_fresh_install_admin_admin_logs_and_manifest(self):
        state,source,values=self.prepare()
        self.assertEqual(state['port'],18888)
        self.assertEqual(values['BYPAY_ADMIN_EMAIL'],'admin')
        self.assertEqual(values['BYPAY_ADMIN_PASSWORD'],'admin')
        row=management.admin_row(values)
        self.assertTrue(check_password_hash(row['password'],'admin'))
        app=create_app({**values,'BYPAY_RUN_MODE':'managed','ALLOW_REGISTRATION':'true','BYPAY_EMAIL_DOMAINS':'example.test'})
        client=app.test_client()
        self.assertEqual(client.post('/api/auth/login',json={'email':'admin','password':'admin'}).status_code,200)
        self.assertEqual(client.post('/api/auth/register',json={'email':'other@example.test','password':'admin'}).status_code,400)
        self.assertEqual(source.stat().st_mode & 0o777,0o600)
        self.assertEqual((Path(values['DATABASE']).parent/'.admin-credentials').stat().st_mode & 0o777,0o600)

    def test_redeployment_retains_credentials_database_and_unrelated_config(self):
        _,source,values=self.prepare()
        set_key(source,'PRESERVE_ME',"literal-$(touch forbidden)-`hello`")
        before=management.admin_row(values)
        self.prepare()
        after=management.admin_row(values)
        self.assertEqual(before,after)
        self.assertEqual(dotenv_values(source)['PRESERVE_ME'],"literal-$(touch forbidden)-`hello`")
        self.assertFalse((self.root/'forbidden').exists())

    def test_existing_local_account_is_not_reset(self):
        source=self.root/'.env.local'
        source.write_text(f"DATABASE='{self.root}/old.sqlite3'\nBYPAY_ADMIN_EMAIL=existing@example.test\nBYPAY_ADMIN_PASSWORD=Existing-password-123\n")
        _,chosen,values=self.prepare()
        self.assertEqual(chosen,source)
        self.assertEqual(management.admin_row(values)['email'],'existing@example.test')
        self.assertFalse((self.root/'.env').exists())

    def test_change_account_password_preserves_user_id_and_revokes_sessions(self):
        _,source,values=self.prepare()
        app=create_app(values);client=app.test_client()
        client.post('/api/auth/login',json={'email':'admin','password':'admin'})
        before=management.admin_row(values)
        password="new-'$()-password"
        with patch('builtins.input',return_value='manager'),patch('getpass.getpass',side_effect=[password,password]),patch('builtins.print'):
            management.change_credentials()
        after=management.admin_row(values)
        self.assertEqual(before['id'],after['id'])
        self.assertEqual(after['email'],'manager')
        self.assertTrue(check_password_hash(after['password'],password))
        self.assertEqual(client.get('/api/auth/me').status_code,401)
        self.assertEqual(client.post('/api/auth/login',json={'email':'manager','password':password}).status_code,200)
        self.assertEqual(dotenv_values(source)['BYPAY_ADMIN_PASSWORD'],password)

    def test_account_only_change_keeps_password_and_unknown_password_is_not_faked(self):
        _,_,values=self.prepare()
        with patch('builtins.input',return_value='renamed'),patch('getpass.getpass',return_value=''),patch('builtins.print'):
            management.change_credentials()
        _,_,values=management.configuration()
        row=management.admin_row(values)
        self.assertEqual(management.known_credentials(values,row),{'account':'renamed','password':'admin'})
        row['password']=generate_password_hash('other-password')
        self.assertIsNone(management.known_credentials(values,row))

    def test_wrong_confirmation_and_account_collision_do_not_change_credentials(self):
        _,_,values=self.prepare();before=management.admin_row(values)
        with patch('builtins.input',return_value='newname'),patch('getpass.getpass',side_effect=['newpass','different']):
            with self.assertRaises(ValueError): management.change_credentials()
        self.assertEqual(before,management.admin_row(values))

    def test_managed_http_access_remains_origin_checked(self):
        _,source,values=self.prepare()
        set_key(source,'PUBLIC_BASE_URL','http://203.0.113.5:18888')
        with patch.dict(os.environ,{'BYPAY_RUN_MODE':'managed','BYPAY_ENV_FILE':str(source),'BYPAY_FRONTEND_PORT':'18888'}): config=runtime.load_runtime()
        app=create_app(config);client=app.test_client()
        body={'email':'admin','password':'admin'}
        self.assertEqual(client.post('/api/auth/login',json=body,headers={'Origin':'http://127.0.0.1:18888'}).status_code,200)
        self.assertEqual(client.post('/api/auth/login',json=body,headers={'Origin':'https://untrusted.test'}).status_code,403)
        self.assertEqual(app.extensions['channels'].payment_config()['notify_url'],'')

    def test_service_is_one_supervisor_and_foreign_service_is_protected(self):
        with patch('web.management.unit_workdir',return_value=''):
            unit=management.unit_text()
        self.assertIn('-m web.service',unit)
        self.assertIn('KillMode=control-group',unit)
        self.assertNotIn('nginx',unit.lower())
        with patch('web.management.unit_workdir',return_value='/another/project'),patch('subprocess.run') as run:
            with self.assertRaises(ValueError): management.stop()
            run.assert_not_called()

    def test_log_view_follows_payment_file_created_after_startup(self):
        _,_,values=self.prepare()
        server=Path(values['DATABASE']).parent/'logs/server.log'
        server.parent.mkdir(parents=True,exist_ok=True)
        server.write_text('server ready\n')
        self.assertFalse(Path(values['BYPAY_LOG_FILE']).exists())
        with patch('web.management.unit_workdir',return_value=''),patch('subprocess.Popen') as start,patch('builtins.print'):
            management.logs()
        self.assertIn(values['BYPAY_LOG_FILE'],start.call_args.args[0])
        self.assertIn('-F',start.call_args.args[0])

    def test_health_from_another_server_cannot_claim_deployment_success(self):
        state,_,values=self.prepare()
        marker=Path(values['DATABASE']).parent/'service.json'
        marker.write_text(json.dumps({'port':state['port'],'pid':12345}))
        with patch('web.management.health',return_value=True),patch('web.management.project_processes',return_value=[]),patch('time.sleep'):
            with self.assertRaises(ValueError):management.wait_ready()
        with patch('web.management.health',return_value=True),patch('web.management.project_processes',return_value=[12345]):
            management.wait_ready()

    def test_update_backup_copies_database_keys_and_settings_and_keeps_only_the_newest_generations(self):
        state,source,values=self.prepare()
        data=Path(values['DATABASE']).parent
        (data/'channel-master.key').write_bytes(b'master-key');(data/'webhook-signing.pem').write_bytes(b'signing-key');(data/'notes.txt').write_text('not a key')
        backups=data/'backups'
        for name in ('update-20200101T000000Z','update-20200102T000000Z','update-20200103T000000Z','update-20200104T000000Z','by-hand','update-notes'):
            (backups/name).mkdir(parents=True)
        with patch('builtins.print') as shown: management.backup()
        said='\n'.join(str(call.args[0]) for call in shown.call_args_list)
        names=sorted(path.name for path in backups.iterdir())
        fresh=[name for name in names if name.startswith('update-2') and name > 'update-2025']
        self.assertEqual(len(fresh),1)
        self.assertEqual(names,sorted(['by-hand','update-20200103T000000Z','update-20200104T000000Z','update-notes',fresh[0]]))  # newest three generations; foreign folders are not ours to delete
        folder=backups/fresh[0]
        self.assertEqual(sorted(path.name for path in folder.iterdir()),['channel-master.key','env','epay-platform-private.pem','launcher.json','merchant-master.key','web.sqlite3','webhook-signing.pem'])  # every key of the install, no other file
        for name in ('channel-master.key','epay-platform-private.pem','merchant-master.key','webhook-signing.pem'): self.assertEqual((folder/name).read_bytes(),(data/name).read_bytes(),name)
        self.assertEqual(dotenv_values(folder/'env')['BYPAY_ADMIN_EMAIL'],'admin')
        for path in folder.iterdir(): self.assertEqual(path.stat().st_mode & 0o777,0o600,path.name)
        self.assertEqual(folder.stat().st_mode & 0o777,0o700)
        with closing(sqlite3.connect(folder/'web.sqlite3')) as copy:
            self.assertEqual(copy.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(copy.execute("SELECT email FROM users WHERE role='admin'").fetchone()[0],'admin')
        self.assertIn(str(folder),said)
        self.assertIn('1 个用户',said)
        self.assertNotIn('master-key',said)

    def test_update_backup_is_a_consistent_copy_of_a_database_that_is_being_written(self):
        _,_,values=self.prepare()
        live=sqlite3.connect(values['DATABASE'],isolation_level=None);self.addCleanup(live.close)
        live.execute('PRAGMA journal_mode=WAL');live.execute('CREATE TABLE IF NOT EXISTS marker(value TEXT)');live.execute("INSERT INTO marker VALUES ('committed')")
        live.execute('BEGIN');live.execute("INSERT INTO marker VALUES ('half-written')")  # a transaction that has not been committed yet
        with patch('builtins.print'): management.backup()
        folder=next((Path(values['DATABASE']).parent/'backups').glob('update-*'))
        with closing(sqlite3.connect(folder/'web.sqlite3')) as copy:
            self.assertEqual([row[0] for row in copy.execute('SELECT value FROM marker')],['committed'])
        live.execute('ROLLBACK')

    def test_update_backup_without_a_database_does_nothing(self):
        with patch('builtins.print') as shown: management.backup()
        self.assertIn('尚无数据库',str(shown.call_args.args[0]))
        self.assertFalse((self.root/'data').exists())

    def test_update_backup_failure_leaves_the_live_database_untouched(self):
        _,_,values=self.prepare()
        before=Path(values['DATABASE']).read_bytes()
        with patch('web.management.shutil.copy2',side_effect=OSError('disk full')),patch('builtins.print'):
            with self.assertRaises(OSError): management.backup()
        self.assertEqual(Path(values['DATABASE']).read_bytes(),before)

    def test_update_is_only_offered_for_a_deployment_with_this_projects_system_service(self):
        with patch('web.management.systemd_running',return_value=True):
            with self.assertRaisesRegex(ValueError,'尚未部署'): management.update_ready()  # nothing prepared yet
        self.prepare()
        with patch('web.management.systemd_running',return_value=False):
            with self.assertRaisesRegex(ValueError,'systemd'): management.update_ready()
        with patch('web.management.systemd_running',return_value=True):
            for other in ('','/another/project'):
                with patch('web.management.unit_workdir',return_value=other):
                    with self.assertRaisesRegex(ValueError,'系统服务'): management.update_ready()
            with patch('web.management.unit_workdir',return_value=str(self.root)): management.update_ready()

    def test_update_backup_copies_a_database_in_a_folder_whose_name_looks_like_a_uri(self):
        _,source,values=self.prepare()
        odd=self.root/'data #1 ?x=y %41 é'
        odd.mkdir()
        shutil.copy2(values['DATABASE'],odd/'web.sqlite3')
        set_key(source,'DATABASE',str(odd/'web.sqlite3'))
        before=sorted(path.name for path in self.root.iterdir())
        with patch('builtins.print') as shown: management.backup()
        folder=next((odd/'backups').glob('update-*'))
        with closing(sqlite3.connect(folder/'web.sqlite3')) as copy:
            self.assertEqual(copy.execute("SELECT email FROM users WHERE role='admin'").fetchone()[0],'admin')  # the real data, not an empty database
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),before)  # and no stray database made at a path cut short at the # or ?
        self.assertIn('1 个用户','\n'.join(str(call.args[0]) for call in shown.call_args_list))

    def test_command_line_actions_for_the_update_report_success_or_failure_by_exit_code(self):
        def run(action,error=None):
            with patch('sys.argv',['management',action]),patch('builtins.print') as shown:
                try: management.main(); code=0
                except SystemExit as stop: code=stop.code
            return code,shown
        self.assertEqual(run('ready')[0],1)  # nothing deployed yet
        self.prepare()
        with patch('web.management.systemd_running',return_value=True),patch('web.management.unit_workdir',return_value=str(self.root)):
            self.assertEqual(run('ready')[0],0)
        code,shown=run('backup')
        self.assertEqual(code,0)
        self.assertEqual(len(list((self.root/'data/backups').glob('update-*'))),1)
        with patch('web.management.backup',side_effect=KeyboardInterrupt):
            self.assertEqual(run('backup')[0],130)  # a cancelled backup must not look like a finished one
        with patch('web.management.backup',side_effect=EOFError):
            self.assertEqual(run('backup')[0],1)


if __name__=='__main__': unittest.main()
