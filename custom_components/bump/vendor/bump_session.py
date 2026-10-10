"""Portable current-session export and isolated phone/code login state."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import uuid
from .bump_api import DEFAULT_ENDPOINT, STATE_DIR, field, scalar, find_auth_tokens, device_details, private_write

PREFIX = 'bump1.'


def token_uid(token):
    if find_auth_tokens(token) != [token]:
        raise ValueError('Invalid signed Bump token')
    uid = scalar(scalar(scalar(token, 2), 2), 1)
    if not isinstance(uid, int) or not 0 < uid < 1 << 64:
        raise ValueError('Invalid token user UID')
    return str(uid)


def export_session(token, *, endpoint=DEFAULT_ENDPOINT, device_id=None):
    uid = token_uid(token)
    if endpoint.rstrip('/') != DEFAULT_ENDPOINT:
        raise ValueError('Unsupported Bump endpoint')
    data = {'version': 1, 'endpoint': DEFAULT_ENDPOINT, 'user_uid': uid,
            'token': base64.b64encode(token).decode()}
    if device_id:
        data['device_id'] = str(uuid.UUID(device_id))
    return PREFIX + base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')


def import_session(value):
    if not isinstance(value, str) or not value.strip().startswith(PREFIX) or len(value) > 65536:
        raise ValueError('Expected a bump1 session export')
    encoded = value.strip()[len(PREFIX):]
    try:
        data = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
        if data['version'] != 1 or data['endpoint'] != DEFAULT_ENDPOINT:
            raise ValueError('Unsupported session format or endpoint')
        token = base64.b64decode(data['token'], validate=True)
        uid = token_uid(token)
        if data['user_uid'] != uid:
            raise ValueError('Session UID does not match token')
        return {'token': token, 'user_uid': uid, 'endpoint': DEFAULT_ENDPOINT,
                'device_id': data.get('device_id')}
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid session export') from error


class PhoneLogin:
    """Per-flow state; no shared files, no replacement login until explicitly requested."""
    def __init__(self, client):
        self.client = client
        self.state = None

    def send_code(self, phone, calling_code, first_name='', last_name='', age=0):
        if not phone.startswith('+') or not phone[1:].isdigit() or not 1 <= calling_code <= 999 or not phone.startswith('+' + str(calling_code)):
            raise ValueError('Phone must include matching country calling code')
        if not isinstance(age, int) or not 0 <= age <= 150:
            raise ValueError('Invalid age')
        self.state = {'phone': phone, 'calling_code': calling_code, 'first_name': first_name,
                      'last_name': last_name, 'age': age, 'secret': os.urandom(16), 'device_id': str(uuid.uuid4())}
        s = self.state
        user = ((field(1, first_name) if first_name else b'') + (field(2, last_name) if last_name else b'')
                + (field(3, age) if age else b'') + field(4, device_details(s['device_id'])))
        payload = (field(1, field(1, phone[len('+' + str(calling_code)):])) + field(2, field(1, calling_code))
                   + field(3, hashlib.sha256(s['secret']).digest()) + field(4, 1) + field(6, 1) + field(7, user))
        try:
            self.client.unary('api.front.verify.Service/CreateCodeSession', payload)
        except Exception:
            self.state = None
            raise

    def verify_code(self, code):
        if not self.state or not isinstance(code, str) or not code.isdigit() or not 4 <= len(code) <= 8:
            raise ValueError('No pending session or invalid code')
        s = self.state
        payload = (field(1, 1) + field(2, field(1, s['phone'][len('+' + str(s['calling_code'])):]))
                   + field(3, field(1, s['calling_code'])) + field(4, s['secret']) + field(5, code)
                   + (field(6, s['first_name']) if s['first_name'] else b'') + (field(7, s['last_name']) if s['last_name'] else b'')
                   + (field(8, s['age']) if s['age'] else b'') + field(9, device_details(s['device_id'])))
        raw = self.client.unary('api.front.verify.Service/ValidateCode', payload)
        tokens = find_auth_tokens(raw)
        if len(tokens) != 1:
            raise ValueError('Server returned no unique auth token')
        exported = export_session(tokens[0], device_id=s['device_id'])
        self.state = None
        return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', type=Path, default=STATE_DIR / 'auth_token.pb')
    parser.add_argument('--output', type=Path, default=STATE_DIR / 'session_export.txt')
    args = parser.parse_args()
    state_path = STATE_DIR / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    exported = export_session(args.token.read_bytes(), device_id=state.get('device_id'))
    private_write(args.output, (exported + '\n').encode())
    print(f'Existing login exported to {args.output}. Paste the complete bump1 string into Home Assistant.')


if __name__ == '__main__':
    main()
