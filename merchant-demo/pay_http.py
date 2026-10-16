"""Bounded HTTPS requests using HTTPX's standard client identity."""
import json

import httpx


def request_json(method, url, *, payload=None, api_key=None, form=False):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        # Keep TLS verification enabled and never forward credentials through redirects.
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
                          trust_env=False) as client:
            kwargs = {"data" if form else "json": payload} if payload is not None else {}
            with client.stream(method, url, headers=headers, **kwargs) as response:
                status = response.status_code
                if 300 <= status < 400:
                    return {"error": "pay_redirect_rejected"}, status
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        return {"error": "pay_response_too_large"}, 502
                try:
                    data = json.loads(raw)
                except ValueError:
                    return {"error": "pay_http_error"}, status if status >= 400 else 502
                if not isinstance(data, dict):
                    return {"error": "pay_invalid_response"}, 502
                if status >= 400:
                    error = data.get("error", "pay_http_error")
                    return {"error": error if isinstance(error, str) else "pay_http_error"}, status
                return data, status
    except (httpx.HTTPError, OSError, ValueError):
        return {"error": "pay_unreachable"}, 502
