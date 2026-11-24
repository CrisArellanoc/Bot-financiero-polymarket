from pathlib import Path
import tempfile
import unittest

from settings import load_settings


class SettingsTests(unittest.TestCase):
    def test_template_and_environment_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comentario\nMODEL_API_KEY='a$b#c'\n"
                "LAYA_MODEL=\"Modelo 1\" # prueba\nSUPERVISOR_API_URL=https://test.example/chat\n"
                "UNRELATED_KEY=private", encoding="utf-8-sig"
            )
            env = {"LAYA_MODEL": "proveedor/modelo", "SUPERVISOR_API_URL": ""}
            settings = load_settings(path, env)
        self.assertEqual(settings, {
            "MODEL_API_KEY": "a$b#c", "LAYA_MODEL": "proveedor/modelo", "SUPERVISOR_API_URL": ""
        })
        self.assertNotIn("MODEL_API_KEY", env)

    def test_errors_do_not_disclose_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for content in ("secret-value", "MODEL_API_KEY=\"secret-value",
                            "MODEL_API_KEY=secret-value\nMODEL_API_KEY=x"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError) as error:
                        load_settings(path, {})
                    self.assertNotIn("secret-value", str(error.exception))

    def test_missing_file_can_use_environment_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"SUPERVISOR_API_KEY": "value", "MODEL_CA_FILE": "/certs/server.pem", "PATH": "ignored"}
            self.assertEqual(load_settings(Path(directory) / ".env", env),
                             {"SUPERVISOR_API_KEY": "value", "MODEL_CA_FILE": "/certs/server.pem"})
