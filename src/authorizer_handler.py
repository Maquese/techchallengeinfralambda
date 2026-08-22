import base64
import hashlib
import hmac
import json
import os
import time


def decode_jwt(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token")
    unsigned = f"{parts[0]}.{parts[1]}".encode()
    expected = base64.urlsafe_b64encode(
        hmac.new(os.environ["JWT_SECRET"].encode(), unsigned, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, parts[2]):
        raise ValueError("Invalid signature")
    payload = json.loads(
        base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
    )
    if (
        payload.get("iss") != os.environ.get("JWT_ISSUER", "GestaoAutoRepara")
        or payload.get("aud") != os.environ.get("JWT_AUDIENCE", "GestaoAutoReparaUsers")
        or int(payload.get("exp", 0)) <= int(time.time())
    ):
        raise ValueError("Invalid claims")
    return payload


def policy(effect, principal, resource, context=None):
    result = {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }
    if context:
        result["context"] = context
    return result


def handler(event, context):
    headers = event.get("headers") or {}
    token = (
        headers.get("Authorization")
        or headers.get("authorization")
        or event.get("authorizationToken")
        or ""
    ).removeprefix("Bearer ").strip()
    try:
        payload = decode_jwt(token)
        return policy(
            "Allow",
            payload.get("sub", "authenticated"),
            event.get("methodArn", "*"),
            {
                "subject": payload.get("sub", ""),
                "type": payload.get("type", ""),
                "role": payload.get("role", ""),
            },
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return policy("Deny", "anonymous", event.get("methodArn", "*"))
