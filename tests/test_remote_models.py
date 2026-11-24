import io
import json
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import remote_models as remote


class RemoteClientsTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"LAYA_API_URL": "https://laya.test/v1/systemone",
                         "SUPERVISOR_API_URL": "https://supervisor.test/v1/chat/completions",
                         "SUPERVISOR_MODEL": "watcher", "MODEL_API_KEY": "shared-test-token"}
        self.values = [.51, .02, .1, 0, .01]
        self.events = {"mensajes": [{"ref": "E0", "hecho": "Mercado abierto."}]}
        self.calls = []

    def respond(self, value):
        def open_request(request, timeout):
            self.calls.append((request.full_url, request.get_header("Authorization"),
                               json.loads(request.data) if request.data else None))
            return io.BytesIO(json.dumps(value).encode())
        return patch.object(remote, "build_opener",
                            return_value=type("Opener", (), {"open": staticmethod(open_request)})())

    def test_systemone_contract_and_independent_auth(self):
        self.settings["LAYA_API_KEY"] = "laya-test-token"
        laya = remote.RemoteLaya.from_env(self.settings)
        supervisor = remote.RemoteSupervisor.from_env(self.settings)
        with self.respond({"answers": {"up": {"type": "noul", "noul": .58}}}):
            self.assertEqual(laya.predict(self.values), .58)
        url, auth, payload = self.calls[-1]
        self.assertEqual((url, auth), (self.settings["LAYA_API_URL"], "Bearer laya-test-token"))
        self.assertEqual(payload["questions"]["up"]["type"], "noul")
        self.assertIn("mid_up", payload["state"]["document"])
        self.assertNotIn("model", payload)
        note = json.dumps({"estado": "observacion", "principal": "E0", "secundario": "ninguno"})
        with self.respond({"choices": [{"finish_reason": "stop", "message": {"content": note}}]}):
            self.assertEqual(supervisor.explain(self.events), ("observacion", "Mercado abierto."))
        url, auth, payload = self.calls[-1]
        self.assertEqual((url, auth), (self.settings["SUPERVISOR_API_URL"], "Bearer shared-test-token"))
        self.assertEqual(payload["model"], "watcher")
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["principal"]["enum"], ["E0"])
        self.assertEqual(schema["properties"]["estado"]["enum"], ["observacion","atencion"])
        self.assertNotIn("shared-test-token", str(payload))

    def test_chat_laya_uses_explicit_model_and_checks_score(self):
        self.settings.update(LAYA_API_FORMAT="chat", LAYA_MODEL="laya-gateway")
        laya = remote.RemoteLaya.from_env(self.settings)
        for output in ('{"p_up":true}', '{"p_up":"0.4"}', '{"p_up":NaN}', '{"p_up":-1}', '{}', 'free text'):
            with self.subTest(output=output), self.respond({"choices": [{"message": {"content": output}}]}):
                with self.assertRaises(remote.RemoteModelError):
                    laya.predict(self.values)
        with self.respond({"choices": [{"message": {"content": '{"p_up":0.4}'}}]}):
            self.assertEqual(laya.predict(self.values), .4)
        self.assertEqual(self.calls[-1][2]["model"], "laya-gateway")

    def test_workspace_sends_state_as_string(self):
        laya = remote.RemoteLaya.from_env({**self.settings, "LAYA_API_FORMAT": "workspace"})
        with self.respond({"answers": {"up": {"type": "noul", "noul": .2}}}):
            self.assertEqual(laya.predict(self.values), .2)
        self.assertIsInstance(self.calls[-1][2]["state"], str)

    def test_supervisor_discovers_only_once_with_its_own_credentials(self):
        self.settings.update(SUPERVISOR_MODEL="", SUPERVISOR_API_KEY="supervisor-test-token")
        supervisor = remote.RemoteSupervisor.from_env(self.settings)
        note = json.dumps({"estado": "observacion", "principal": "E0", "secundario": "ninguno"})
        reply = {"choices": [{"message": {"content": note}}]}
        with patch.object(remote, "build_opener") as mock:
            mock.return_value.open.side_effect = [io.BytesIO(json.dumps(value).encode()) for value in
                                                  ({"data": [{"id": "my-served-model"}]}, reply, reply)]
            supervisor.explain(self.events)
            supervisor.explain(self.events)
            calls = [call.args[0] for call in mock.return_value.open.call_args_list]
            self.assertEqual([req.get_method() for req in calls], ["GET", "POST", "POST"])
            self.assertEqual(calls[0].full_url, "https://supervisor.test/v1/models")
            self.assertEqual(calls[0].get_header("Authorization"), "Bearer supervisor-test-token")
            self.assertEqual(json.loads(calls[-1].data)["model"], "my-served-model")

    def test_ca_file_must_be_loadable(self):
        with self.assertRaisesRegex(remote.RemoteModelError, "MODEL_CA_FILE"):
            remote.RemoteLaya.from_env({**self.settings, "MODEL_CA_FILE": "missing-ca-remote-models-test.pem"})

    def test_independent_timeouts_and_sanitized_timeout_error(self):
        self.assertEqual(remote.RemoteLaya.from_env(self.settings).endpoint.timeout,90)
        self.assertEqual(remote.RemoteSupervisor.from_env(self.settings).endpoint.timeout,30)
        client = remote.RemoteLaya.from_env({**self.settings,"LAYA_TIMEOUT_SECONDS":"120"})
        with patch.object(remote,"build_opener") as mocked:
            for exc in (TimeoutError("private error"),URLError(TimeoutError("private error"))):
                mocked.return_value.open.side_effect=exc
                with self.assertRaisesRegex(remote.RemoteModelError,"120 s") as error:
                    client.predict(self.values)
                self.assertNotIn("private",str(error.exception))
                self.assertEqual(mocked.return_value.open.call_args.kwargs["timeout"],120)
        for value in ("NaN","inf","0","241","private invalid"):
            with self.subTest(value=value),self.assertRaisesRegex(remote.RemoteModelError,"LAYA_TIMEOUT_SECONDS"):
                remote.RemoteLaya.from_env({**self.settings,"LAYA_TIMEOUT_SECONDS":value})

    def test_auth_and_transport_errors_are_safe(self):
        with patch.object(remote, "build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError(
                "https://example.test", 401, "sensitive-echo", {}, io.BytesIO(b"sensitive body"))
            with self.assertRaisesRegex(remote.RemoteModelError, "HTTP 401") as ctx:
                remote.RemoteLaya.from_env(self.settings).predict(self.values)
            self.assertNotIn("sensitive", str(ctx.exception))
        endpoint = remote.RemoteLaya.from_env(self.settings).endpoint
        self.assertNotIn(self.settings["MODEL_API_KEY"], repr(endpoint))
        self.assertIsNone(remote._NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.test"))
        for url in ("http://example.test", "https://user:secret@example.test", "", "https://example.test:bad"):
            with self.subTest(url=url), self.assertRaisesRegex(remote.RemoteModelError, "LAYA_API_URL"):
                remote.RemoteLaya.from_env({**self.settings, "LAYA_API_URL": url})

    def test_supervisor_cannot_return_unobserved_facts(self):
        note = json.dumps({"estado": "atencion", "principal": "E91", "secundario": "ninguno"})
        with self.respond({"choices": [{"message": {"content": note}}]}), self.assertRaises(remote.RemoteModelError):
            remote.RemoteSupervisor.from_env(self.settings).explain(self.events)

    def test_supervisor_accepts_fenced_json_but_rejects_incomplete_and_unobserved_content(self):
        note = '{"estado":"observacion","principal":"E0","secundario":"ninguno"}'
        supervisor = remote.RemoteSupervisor.from_env(self.settings)
        for content in (note, "```json\n"+note+"\n```", "\n```\n"+note+"\n```\n"):
            with self.respond({"choices":[{"finish_reason":"stop","message":{"content":content}}]}):
                self.assertEqual(supervisor.explain(self.events),("observacion","Mercado abierto."))
        for content in ("```json\n"+note, "Explicación\n"+note, "```json\n"+note+"\n```\nTexto"):
            with self.respond({"choices":[{"message":{"content":content}}]}), self.assertRaises(remote.RemoteModelError):
                supervisor.explain(self.events)
        with self.respond({"choices":[{"finish_reason":"length","message":{"content":note}}]}), \
             self.assertRaisesRegex(remote.RemoteModelError,"límite"):
            supervisor.explain(self.events)

    def test_certificate_failure_has_distinct_sanitized_diagnostic(self):
        with patch.object(remote, "build_opener") as mock:
            mock.return_value.open.side_effect = URLError(ssl.SSLCertVerificationError("sensitive certificate info"))
            with self.assertRaisesRegex(remote.RemoteModelError, "MODEL_CA_FILE") as ctx:
                remote.RemoteLaya.from_env(self.settings).predict(self.values)
            self.assertNotIn("sensitive", str(ctx.exception))
