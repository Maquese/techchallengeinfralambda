import os
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("JWT_ISSUER", "test-issuer")
os.environ.setdefault("JWT_AUDIENCE", "test-audience")

from auth_handler import normalize_cpf, sign_jwt, decode_jwt


class AuthHandlerTests(unittest.TestCase):
    def test_normalize_valid_cpf(self):
        self.assertEqual(normalize_cpf("529.982.247-25"), "52998224725")

    def test_normalize_invalid_cpf(self):
        self.assertIsNone(normalize_cpf("111.111.111-11"))

    def test_jwt_round_trip(self):
        token = sign_jwt({"sub": "user", "iss": "test-issuer", "aud": "test-audience", "exp": 4102444800})
        self.assertEqual(decode_jwt(token)["sub"], "user")


if __name__ == "__main__":
    unittest.main()