import io
import json
import unittest
from unittest.mock import patch

import remote_brain as brain
import remote_models as remote


class RemoteBrainTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"SUPERVISOR_API_URL":"https://teacher.test/v1/chat/completions",
                         "SUPERVISOR_MODEL":"teacher-llm","MODEL_API_KEY":"private-test-token"}
        self.evidence = {"mensajes":[{"ref":"E0","hecho":"Libro Up observado antes de abrir."},
                                      {"ref":"E1","hecho":"TWAP BTC recibido de Polymarket."}],
                         "modo":"simulación"}
        self.decision = {"action":"Up","p_up":.62,"reason":"La evidencia E0 favorece Up.","refs":["E0"]}

    def test_uses_teacher_chat_settings_and_strict_output_contract(self):
        client = brain.RemoteBrain.from_env(self.settings)
        payloads = []
        def respond(request,timeout):
            payloads.append((request,json.loads(request.data)))
            return io.BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{
                "content":json.dumps(self.decision)}}]}).encode())
        with patch.object(remote,"build_opener") as opener:
            opener.return_value.open.side_effect=respond
            self.assertEqual(client.decide(self.evidence),self.decision)
        req,body=payloads[0]
        self.assertEqual(req.full_url,self.settings["SUPERVISOR_API_URL"])
        self.assertEqual(req.get_header("Authorization"),"Bearer private-test-token")
        self.assertEqual(body["model"],"teacher-llm")
        self.assertEqual(body["temperature"],0)
        self.assertNotIn("tools",body)
        self.assertNotIn("private-test-token",json.dumps(body))
        schema=body["response_format"]["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["refs"]["items"]["enum"],["E0","E1"])
        self.assertIn("NO calibrada",body["messages"][0]["content"])
        self.assertNotIn("private-test-token",repr(client.endpoint))

    def test_exploration_requests_direction_and_preserves_uncertainty(self):
        client=brain.RemoteBrain.from_env(self.settings)
        decision={**self.decision,"p_up":.5}
        evidence={**self.evidence,"modo":"exploracion_paper"}
        with patch.object(remote._Endpoint,"chat",return_value=decision) as api:
            self.assertEqual(client.decide(evidence),decision)
            self.assertEqual(api.call_args.args[2]["properties"]["action"]["enum"],["Up","Down"])
            api.return_value={**decision,"action":"abstain"}
            with self.assertRaises(remote.RemoteModelError):client.decide(evidence)

    def test_probability_first_compares_two_pairs_and_validates_outputs(self):
        self.assertCountEqual([brain.exploration_version(t) for t in range(3600,4800,300)],
                              [brain.PROBABILITY_VERSION]*2+[brain.EXPLORATION_VERSION]*2)
        client=brain.RemoteBrain.from_env(self.settings)
        evidence={**self.evidence,"modo":"exploracion_paper","forecast_method":"probability_first"}
        for p,action in ((.49,"Down"),(.50,"Up"),(.51,"Up")):
            reply=dict(p_up=p,reason="E0",refs=["E0"])
            with patch.object(remote._Endpoint,"chat",return_value=reply) as api:
                self.assertEqual(client.decide(evidence)["action"],action)
                self.assertNotIn("action",api.call_args.args[2]["properties"])
                for bad in ({**reply,"p_up":True},{**reply,"p_up":float("nan")},{**reply,"action":"Up"}):
                    api.return_value=bad
                    with self.assertRaises(remote.RemoteModelError): client.decide(evidence)

    def test_rejects_malformed_unobserved_and_inconsistent_decisions(self):
        for change in ({"action":"BUY"},{"action":"Down"},{"p_up":True},{"p_up":".6"},
                       {"p_up":float("nan")},{"p_up":None},{"p_up":.5},{"p_up":1.1},
                       {"refs":["E99"]},{"refs":[]},{"refs":["E0","E0"]},{"refs":"E0"},
                       {"reason":""},{"reason":"x"*241},{"reason":"line\nbreak"},
                       {"extra":"private-test-token"}):
            with self.subTest(change=change), self.assertRaises(remote.RemoteModelError) as ctx:
                brain.validate_decision({**self.decision,**change},["E0","E1"])
            self.assertNotIn("private-test-token",str(ctx.exception))
        for value in (None,[],{},"private-test-token"):
            with self.assertRaises(remote.RemoteModelError):
                brain.validate_decision(value,["E0"])

    def test_abstention_and_down_are_allowed_without_rewriting_the_evidence(self):
        for change in ({"action":"abstain","p_up":None},{"action":"abstain","p_up":.5},
                       {"action":"Down","p_up":.4}):
            decision={**self.decision,**change}
            self.assertEqual(brain.validate_decision(decision,["E0","E1"]),decision)
        before=json.dumps(self.evidence)
        client=brain.RemoteBrain.from_env(self.settings)
        with patch.object(remote._Endpoint,"chat",return_value=self.decision):
            client.decide(self.evidence)
        self.assertEqual(json.dumps(self.evidence),before)

    def test_invalid_evidence_never_calls_the_server(self):
        for evidence in ({},None,{"mensajes":[]},{"mensajes":[{"ref":"E0","hecho":"ok"}]*2},
                         {"mensajes":[{"ref":"E0","hecho":"ok"}],"value":float("nan")}):
            client=brain.RemoteBrain.from_env(self.settings)
            with patch.object(remote._Endpoint,"chat") as api,self.assertRaises(remote.RemoteModelError):
                client.decide(evidence)
            api.assert_not_called()

    def test_discovers_the_remote_model_only_once(self):
        client=brain.RemoteBrain.from_env({**self.settings,"SUPERVISOR_MODEL":""})
        with patch.object(remote._Endpoint,"discover_model",return_value="discovered") as discover, \
             patch.object(remote._Endpoint,"chat",return_value=self.decision):
            client.decide(self.evidence)
            client.decide(self.evidence)
        discover.assert_called_once()
        self.assertEqual(client.name,"discovered")

    def test_opening_deadline_bounds_discovery_and_inference_without_changing_configuration(self):
        client=brain.RemoteBrain.from_env({**self.settings,"SUPERVISOR_MODEL":""})
        seen=[]
        def discover(endpoint):
            seen.append(endpoint.timeout)
            return "discovered"
        def chat(endpoint,*args):
            seen.append(endpoint.timeout)
            return self.decision
        with patch.object(remote._Endpoint,"discover_model",discover), \
             patch.object(remote._Endpoint,"chat",chat), \
             patch("remote_brain.time.monotonic",side_effect=[100,103]):
            client.decide(self.evidence,timeout=10)
        self.assertEqual(seen,[10,7])
        self.assertEqual(client.endpoint.timeout,30)
        with patch.object(remote._Endpoint,"chat") as api, \
             patch("remote_brain.time.monotonic",side_effect=[100,111]), \
             self.assertRaisesRegex(remote.RemoteModelError,"plazo"):
            client.decide(self.evidence,timeout=10)
        api.assert_not_called()
