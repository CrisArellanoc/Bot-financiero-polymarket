"""Clientes HTTPS de inferencia. Solo biblioteca estándar, sin pesos locales."""
from dataclasses import dataclass, field, replace
from http.client import HTTPException
import json
import math
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class RemoteModelError(ValueError):
    """Mensaje seguro para el registro del panel."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Nunca reenviar un token ni la evidencia a un segundo destino.
        return None


@dataclass(frozen=True)
class _Endpoint:
    url: str = field(repr=False)
    key: str = field(repr=False)
    model: str
    context: ssl.SSLContext | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings, prefix, require_model=True):
        url = settings.get(prefix + "_API_URL", "").strip()
        key = (settings.get(prefix + "_API_KEY") or settings.get("MODEL_API_KEY") or "").strip()
        model = settings.get(prefix + "_MODEL", "").strip()
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname and not parsed.fragment
                     and parsed.username is None and parsed.password is None
                     and all(ord(c) > 32 and ord(c) != 127 for c in url))
            _ = parsed.port  # Valida también un puerto especificado explícitamente.
        except ValueError:
            valid = False
        if not valid:
            raise RemoteModelError(f"Configura {prefix}_API_URL con la URL HTTPS completa")
        if not key or not key.isascii() or not all(33 <= ord(ch) <= 126 for ch in key):
            raise RemoteModelError(f"Configura {prefix}_API_KEY o MODEL_API_KEY con el token Bearer")
        if require_model and not model:
            raise RemoteModelError(f"Configura {prefix}_MODEL con el identificador del proveedor")
        ca_file = settings.get("MODEL_CA_FILE", "").strip()
        context = None
        if ca_file:
            try:
                context = ssl.create_default_context()
                context.load_verify_locations(cafile=ca_file)
            except (OSError, ValueError):
                raise RemoteModelError("MODEL_CA_FILE debe apuntar a un certificado CA PEM legible") from None
        return cls(url, key, model, context)

    def request(self, url, payload=None):
        try:
            data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
            request = Request(url, data=data,
                              headers={"Authorization": "Bearer " + self.key,
                                       "Content-Type": "application/json", "Accept": "application/json"})
            with build_opener(_NoRedirect(), HTTPSHandler(context=self.context)).open(request, timeout=30) as response:
                raw = response.read(65537)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise RemoteModelError(f"El servidor de inferencia respondió HTTP {status}") from None
        except (URLError, ssl.SSLCertVerificationError) as exc:
            if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
                raise RemoteModelError("No se pudo verificar el certificado TLS: comprueba el nombre del "
                                       "servidor y la CA de confianza (MODEL_CA_FILE)") from None
            raise RemoteModelError("Falló la conexión HTTPS con el servidor de inferencia") from None
        except (OSError, ValueError, HTTPException):
            raise RemoteModelError("Falló la conexión HTTPS con el servidor de inferencia") from None
        if len(raw) > 65536:
            raise RemoteModelError("Respuesta de inferencia demasiado grande")
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            raise RemoteModelError("Respuesta de inferencia con JSON inválido") from None
        if not isinstance(value, dict):
            raise RemoteModelError("Se esperaba un objeto JSON del servidor de inferencia")
        return value

    def discover_model(self):
        parsed = urlsplit(self.url)
        path = parsed.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            raise RemoteModelError("Configura SUPERVISOR_MODEL para esta ruta de chat personalizada")
        models_url = parsed._replace(path=path.removesuffix("/chat/completions") + "/models").geturl()
        catalog = self.request(models_url).get("data")
        if isinstance(catalog, list) and catalog and isinstance(catalog[0], dict):
            first = catalog[0].get("id")
            if isinstance(first, str) and first.strip():
                return first
        raise RemoteModelError("El catálogo remoto no devolvió un identificador de modelo")

    def chat(self, instruction, evidence):
        response = self.request(self.url, {"model": self.model, "stream": False,
                              "temperature": 0, "max_tokens": 192,
                              "messages": [{"role": "system", "content": instruction},
                                           {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]})
        try:
            choice = response["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError()
            value = json.loads(choice["message"]["content"])
            if not isinstance(value, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
            raise RemoteModelError("Chat remoto sin respuesta JSON válida y completa") from None
        return value


class RemoteLaya:
    def __init__(self, endpoint, protocol):
        self.endpoint = endpoint
        self.protocol = protocol
        self.name = endpoint.model or "Laya System One"

    @classmethod
    def from_env(cls, settings):
        protocol = settings.get("LAYA_API_FORMAT", "systemone").strip().lower()
        if protocol not in ("workspace", "systemone", "chat"):
            raise RemoteModelError("LAYA_API_FORMAT admite workspace, systemone o chat")
        return cls(_Endpoint.from_settings(settings, "LAYA", require_model=protocol == "chat"), protocol)

    def predict(self, values):
        from laya_shadow import make_state, QUESTION
        state = make_state(values)
        if self.protocol in ("workspace", "systemone"):
            payload = {"state": state["document"] if self.protocol == "workspace" else state,
                       "questions": QUESTION}
            if self.endpoint.model:
                payload["model"] = self.endpoint.model
            response = self.endpoint.request(self.endpoint.url, payload)
            try:
                answer = response["answers"]["up"]["noul"]
            except (KeyError, TypeError):
                raise RemoteModelError("Laya System One omitió answers.up.noul") from None
        else:
            instruction = (QUESTION["up"]["instructions"] +
                           ' Respond only with JSON {"p_up": NUMBER}, a score between 0 and 1.')
            answer = self.endpoint.chat(instruction, state).get("p_up")
        if type(answer) not in (int, float) or not 0 <= answer <= 1 or not math.isfinite(answer):
            raise RemoteModelError("Laya devolvió una puntuación inválida")
        return float(answer)


class RemoteSupervisor:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.name = endpoint.model or "Supervisor remoto (automático)"

    @classmethod
    def from_env(cls, settings):
        return cls(_Endpoint.from_settings(settings, "SUPERVISOR", require_model=False))

    def explain(self, evidence):
        from local_observer import INSTRUCTIONS, validate_note
        if not self.endpoint.model:
            model = self.endpoint.discover_model()
            self.endpoint = replace(self.endpoint, model=model)
            self.name = model
        instruction = INSTRUCTIONS + ' Devuelve solo JSON con los campos "estado", "principal" y "secundario".'
        note = self.endpoint.chat(instruction, evidence)
        try:
            return validate_note(note, evidence)
        except (ValueError, KeyError, TypeError):
            raise RemoteModelError("El supervisor devolvió un estado o referencias de eventos inválidos") from None
