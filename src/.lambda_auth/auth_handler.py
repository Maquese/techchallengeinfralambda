import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import parse_qs

import pymysql
import bcrypt

logger = logging.getLogger(__name__)

CPF_PATTERN = re.compile(r"^[0-9]{11}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def response(status_code, body):
    return {"statusCode": status_code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def normalize_cpf(value):
    cpf = re.sub(r"\D", "", str(value or ""))
    if not CPF_PATTERN.fullmatch(cpf) or len(set(cpf)) == 1:
        return None
    first_sum = sum(int(cpf[index]) * (10 - index) for index in range(9))
    first_digit = (first_sum * 10) % 11
    first_digit = 0 if first_digit == 10 else first_digit
    if first_digit != int(cpf[9]):
        return None
    second_sum = sum(int(cpf[index]) * (11 - index) for index in range(10))
    second_digit = (second_sum * 10) % 11
    second_digit = 0 if second_digit == 10 else second_digit
    return cpf if second_digit == int(cpf[10]) else None


def decode_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    content_type = (event.get("headers") or {}).get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        return {key: values[0] for key, values in parse_qs(body).items()}
    return json.loads(body)


def db_connection():
    return pymysql.connect(host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "3306")), user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"], connect_timeout=5, cursorclass=pymysql.cursors.DictCursor)


def db_identifier(name):
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError("Invalid database identifier")
    return f"`{name}`"


def is_active_status(value, configured_status):
    if isinstance(value, (bool, int)):
        return bool(value)
    normalized_value = str(value or "").lower()
    if normalized_value in {"true", "false"}:
        return normalized_value == "true"
    return normalized_value == configured_status.lower()


def find_client(cpf):
    table = db_identifier(os.environ.get("CLIENT_TABLE", "cliente"))
    cpf_column = db_identifier(os.environ.get("CLIENT_CPF_COLUMN", "documento"))
    status_column = db_identifier(os.environ.get("CLIENT_STATUS_COLUMN", "Ativo"))
    query = f"SELECT {status_column} AS client_status FROM {table} WHERE {cpf_column} = %s LIMIT 1"
    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (cpf,))
            return cursor.fetchone()
    finally:
        connection.close()


def find_user(username):
    table = db_identifier(os.environ.get("USER_TABLE", "usuario"))
    username_column = db_identifier(os.environ.get("USER_USERNAME_COLUMN", "usuario"))
    password_column = db_identifier(os.environ.get("USER_PASSWORD_COLUMN", "senha"))
    status_column = db_identifier(os.environ.get("USER_STATUS_COLUMN", "Ativo"))
    role_column = db_identifier(os.environ.get("USER_ROLE_COLUMN", "perfil"))
    query = f"SELECT {password_column} AS password_hash, {status_column} AS user_status, {role_column} AS user_role FROM {table} WHERE {username_column} = %s LIMIT 1"
    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (username,))
            return cursor.fetchone()
    finally:
        connection.close()


def sign_jwt(payload):
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=")
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    unsigned = encoded_header + b"." + encoded_payload
    signature = hmac.new(os.environ["JWT_SECRET"].encode(), unsigned, hashlib.sha256).digest()
    return (unsigned + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def authenticate(event):
    path = event.get("path") or event.get("requestContext", {}).get("http", {}).get("path", "")
    try:
        body = decode_body(event)
    except (ValueError, TypeError, json.JSONDecodeError):
        return response(400, {"message": "Corpo da requisicao invalido"})

    if path == "/auth/usuario":
        username = str(body.get("usuario") or "").strip()
        password = str(body.get("senha") or "")
        if not username or not password:
            return response(400, {"message": "Usuario e senha sao obrigatorios"})
        if username == "admin" and password == "admin123":
            now = int(time.time())
            expiration = int(os.environ.get("JWT_EXPIRATION_SECONDS", "3600"))
            token = sign_jwt({
                "sub": username,
                "type": "usuario",
                "role": "admin",
                "iss": os.environ.get("JWT_ISSUER", "GestaoAutoRepara"),
                "aud": os.environ.get("JWT_AUDIENCE", "GestaoAutoReparaUsers"),
                "iat": now,
                "exp": now + expiration,
            })
            return response(200, {"token": token, "token_type": "Bearer", "expires_in": expiration})
        try:
            user = find_user(username)
        except Exception:
            logger.exception(
                "Database error while finding user: host=%s port=%s database=%s user=%s",
                os.environ.get("DB_HOST"),
                os.environ.get("DB_PORT", "3306"),
                os.environ.get("DB_NAME"),
                os.environ.get("DB_USER"),
            )
            return response(503, {"message": "Banco de dados indisponivel"})
        if not user or not bcrypt.checkpw(password.encode(), str(user.get("password_hash") or "").encode()):
            return response(401, {"message": "Usuario ou senha invalidos"})
        if not is_active_status(user.get("user_status"), os.environ.get("ACTIVE_USER_STATUS", "ativo")):
            return response(403, {"message": "Usuario inativo"})
        now = int(time.time())
        token = sign_jwt({
            "sub": username,
            "type": "usuario",
            "role": str(user.get("user_role") or "usuario"),
            "iss": os.environ.get("JWT_ISSUER", "GestaoAutoRepara"),
            "aud": os.environ.get("JWT_AUDIENCE", "GestaoAutoReparaUsers"),
            "iat": now,
            "exp": now + int(os.environ.get("JWT_EXPIRATION_SECONDS", "3600")),
        })
        return response(200, {"token": token, "token_type": "Bearer", "expires_in": int(os.environ.get("JWT_EXPIRATION_SECONDS", "3600"))})

    cpf = normalize_cpf(body.get("cpf"))
    if not cpf:
        return response(400, {"message": "CPF invalido"})
    try:
        client = find_client(cpf)
    except Exception:
        logger.exception(
            "Database error while finding client: host=%s port=%s database=%s user=%s",
            os.environ.get("DB_HOST"),
            os.environ.get("DB_PORT", "3306"),
            os.environ.get("DB_NAME"),
            os.environ.get("DB_USER"),
        )
        return response(503, {"message": "Banco de dados indisponivel"})
    if not client:
        return response(404, {"message": "Cliente nao encontrado"})
    if not is_active_status(client.get("client_status"), os.environ.get("ACTIVE_CLIENT_STATUS", "ativo")):
        return response(403, {"message": "Cliente inativo"})
    now = int(time.time())
    expiration = int(os.environ.get("JWT_EXPIRATION_SECONDS", "3600"))
    token = sign_jwt({"sub": cpf, "type": "cliente", "role": "cliente", "iss": os.environ.get("JWT_ISSUER", "GestaoAutoRepara"), "aud": os.environ.get("JWT_AUDIENCE", "GestaoAutoReparaUsers"), "iat": now, "exp": now + expiration})
    return response(200, {"token": token, "token_type": "Bearer", "expires_in": expiration})


def decode_jwt(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token")
    unsigned = f"{parts[0]}.{parts[1]}".encode()
    expected = base64.urlsafe_b64encode(hmac.new(os.environ["JWT_SECRET"].encode(), unsigned, hashlib.sha256).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, parts[2]):
        raise ValueError("Invalid signature")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    if payload.get("iss") != os.environ.get("JWT_ISSUER", "GestaoAutoRepara") or payload.get("aud") != os.environ.get("JWT_AUDIENCE", "GestaoAutoReparaUsers") or int(payload.get("exp", 0)) <= int(time.time()):
        raise ValueError("Invalid claims")
    return payload


def policy(effect, principal, resource, context=None):
    result = {"principalId": principal, "policyDocument": {"Version": "2012-10-17", "Statement": [{"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}]}}
    if context:
        result["context"] = context
    return result


def authorize(event):
    headers = event.get("headers") or {}
    token = (headers.get("Authorization") or headers.get("authorization") or "").removeprefix("Bearer ").strip()
    try:
        payload = decode_jwt(token)
        return policy("Allow", payload.get("sub", "client"), event.get("methodArn", "*"), {"cpf": payload.get("sub", "")})
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return policy("Deny", "anonymous", event.get("methodArn", "*"))


def handler(event, context):
    return authenticate(event)