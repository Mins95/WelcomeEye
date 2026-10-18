"""Bounded HTTPS account login and independent QV push subscription client.

Auth responses use the APK's cookie-bound HTTP down channel. The up-channel
HTTP acknowledgement is deliberately never treated as a successful login.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .qv_cloud_protocol import (
    ALARM_PATH, AUTH_DOWN_PATH, AUTH_UP_PATH, CONNECT3_PROFILE, MAX_RESPONSE_BYTES,
    OAUTH_PATH, PUSH_INFO_PATH, PUSH_TOKEN_PATH, QvCloudDevice, QvCloudError,
    QvCloudProfile, QvCloudServices, QvRegionRedirect, build_alarm_login,
    build_device_list_request, build_login_request, build_oauth_params,
    build_push_info, build_push_token, build_subscription_request, make_client_id,
    parse_device_list, parse_envelope, parse_login_response, parse_oauth_response,
    password_hash, validate_alarm_response, validate_openapi_response,
)

REQUEST_TIMEOUT = 15.0
RPC_TIMEOUT = 20.0
POLL_TIMEOUT = 45.0
CLOSE_TIMEOUT = 5.0
# UserApi and AlarmApi declare these as separate headers. The QV service
# rejects the otherwise valid combined MIME parameter with a plaintext
# BeginResponseGenerator error before returning a correlated XML response.
XML_HEADERS = {'Content-Type': 'application/xml', 'charset': 'utf-8'}


class QvCloudClient:
    """Own only one HA installation; no phone tokens, device binding or logout."""

    def __init__(self, services: QvCloudServices, installation_id: str, *,
                 profile: QvCloudProfile = CONNECT3_PROFILE,
                 resolver: Callable[[int], Awaitable[QvCloudServices]] | None = None,
                 session_factory=None):
        if not isinstance(services, QvCloudServices):
            raise QvCloudError('invalid_service')
        self.services = services
        self.profile = profile
        self.client_id = make_client_id(installation_id, profile)
        self._resolver = resolver
        self._session_factory = session_factory
        self._sessions = {}
        self._account = None
        self._email = self._digest = self._oauth = None
        self._devices = {}
        self._registered = False
        self._poll_task = None
        self._poll_ready = asyncio.Event()
        self._poll_error = None
        self._pending = {}
        self._sequence = 0
        self._rpc_lock = asyncio.Lock()
        self._closed = False
        self._close_task = None

    def _session(self, kind):
        if self._closed:
            raise QvCloudError('closed')
        if kind not in self._sessions:
            if self._session_factory is not None:
                self._sessions[kind] = self._session_factory(kind)
            else:
                import aiohttp
                # Separate jars match the app: alarm cookies are not auth cookies.
                # Never use HA's global HTTP session for these account sessions.
                self._sessions[kind] = aiohttp.ClientSession(
                    cookie_jar=aiohttp.CookieJar(), trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                )
        return self._sessions[kind]

    async def _request(self, kind, method, base, path, *, timeout=REQUEST_TIMEOUT,
                       data=None, json=None, params=None, headers=None,
                       on_headers: Callable[[], None] | None = None):
        import aiohttp
        session = self._session(kind)
        try:
            async with asyncio.timeout(timeout):
                async with session.request(
                    method, base.rstrip('/') + path, data=data, json=json,
                    params=params, headers=headers, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout), ssl=True,
                ) as response:
                    if response.status in (401, 403):
                        raise QvCloudError('authentication_failed')
                    if response.status != 200:
                        raise QvCloudError('provider_rejected')
                    # aiohttp has applied Set-Cookie before entering this block.
                    # A down-channel body may wait for the first up-channel RPC.
                    if on_headers is not None:
                        on_headers()
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise QvCloudError('response_too_large')
                    if self._closed:
                        raise QvCloudError('closed')
                    return bytes(body)
        except TimeoutError:
            raise QvCloudError('request_timeout') from None
        except (aiohttp.ClientError, OSError, UnicodeError):
            raise QvCloudError('connection_failed') from None

    async def _poll(self):
        try:
            while not self._closed:
                started = asyncio.get_running_loop().time()
                payload = await self._request('auth', 'GET', self.services.auth_url,
                                              AUTH_DOWN_PATH, timeout=POLL_TIMEOUT,
                                              on_headers=self._poll_ready.set)
                if payload.strip():
                    root = parse_envelope(payload)
                    seq_text = root.findtext('header/seq')
                    # Notifications for the phone/account are not RPC responses.
                    if seq_text is not None:
                        try:
                            sequence = int(seq_text)
                        except ValueError:
                            raise QvCloudError('invalid_response') from None
                        pending = self._pending.get(sequence)
                        if pending is not None and not pending.done():
                            pending.set_result(payload)
                # Bound empty-response churn when a provider stops long-polling.
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed < 0.1:
                    await asyncio.sleep(0.1 - elapsed)
        except asyncio.CancelledError:
            raise
        except QvCloudError as err:
            self._poll_error = err.code
            self._poll_ready.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(QvCloudError(err.code))

    async def _rpc(self, builder):
        async with self._rpc_lock:
            if self._closed:
                raise QvCloudError('closed')
            if self._poll_task is None:
                self._poll_task = asyncio.create_task(self._poll(), name='welcomeeye-qv-auth')
            try:
                async with asyncio.timeout(RPC_TIMEOUT):
                    await self._poll_ready.wait()
                    if self._closed:
                        raise QvCloudError('closed')
                    if self._poll_error:
                        raise QvCloudError(self._poll_error)
                    self._sequence += 1
                    sequence = self._sequence
                    payload = builder(sequence)
                    future = asyncio.get_running_loop().create_future()
                    self._pending[sequence] = future
                    try:
                        await self._request('auth', 'POST', self.services.auth_url,
                                            AUTH_UP_PATH, data=payload,
                                            headers=XML_HEADERS)
                        response = await future
                        if self._closed:
                            raise QvCloudError('closed')
                        return response
                    finally:
                        self._pending.pop(sequence, None)
                        if not future.done():
                            future.cancel()
                        elif not future.cancelled():
                            future.exception()  # Consume an error if POST failed first.
            except TimeoutError:
                raise QvCloudError('request_timeout') from None

    async def _stop_auth(self):
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        session = self._sessions.get('auth')
        if session is not None:
            await session.close()
            self._sessions.pop('auth', None)
        self._poll_ready.clear()
        self._poll_error = None

    async def async_login(self, email, password):
        """Authenticate normal credentials; at most one server-directed region retry."""
        if self._closed:
            raise QvCloudError('closed')
        digest = password_hash(password)
        self._devices.clear()
        self._account = None
        self._registered = False
        for attempt in range(2):
            payload = await self._rpc(lambda seq: build_login_request(
                self.client_id, email, digest, self.services.region_id,
                seq=seq, profile=self.profile,
            ))
            try:
                account = parse_login_response(payload)
            except QvRegionRedirect as redirect:
                if attempt or self._resolver is None:
                    raise
                await self._stop_auth()
                services = await self._resolver(redirect.region_id)
                if not isinstance(services, QvCloudServices) or services.region_id != redirect.region_id:
                    raise QvCloudError('invalid_service')
                self.services = services
                continue
            self._account, self._email, self._digest = account, email, digest
            return account
        raise QvCloudError('authentication_failed')

    def _require_account(self):
        if self._closed:
            raise QvCloudError('closed')
        if self._account is None:
            raise QvCloudError('not_authenticated')
        return self._account

    async def async_list_devices(self):
        account = self._require_account()
        payload = await self._rpc(lambda seq: build_device_list_request(
            self.client_id, account.session_id, seq=seq, profile=self.profile,
        ))
        devices = parse_device_list(payload)
        self._devices = {device.uid: device for device in devices if device.cloud_type == 0}
        return tuple(self._devices.values())

    async def async_register(self, fcm_token):
        """Enroll only this fresh, persisted HA client ID and its own FCM token."""
        account = self._require_account()
        params = build_oauth_params(self.client_id, self._email, self._digest,
                                    self.services.region_id, self.profile)
        payload = await self._request('openapi', 'GET', self.services.oauth_url,
                                      OAUTH_PATH, params=params)
        self._oauth = parse_oauth_response(payload)
        headers = {'token': self._oauth.access_token}
        payload = await self._request('openapi', 'POST', self.services.openapi_url,
                                      PUSH_INFO_PATH, headers=headers,
                                      json=build_push_info(self.client_id, account.account_id,
                                                           profile=self.profile))
        validate_openapi_response(payload)
        payload = await self._request('openapi', 'POST', self.services.openapi_url,
                                      PUSH_TOKEN_PATH, headers=headers,
                                      json=build_push_token(self.client_id, fcm_token, self.profile))
        validate_openapi_response(payload)
        payload = await self._request('alarm', 'POST', self.services.alarm_url,
                                      ALARM_PATH, data=build_alarm_login(
                                          self.client_id, fcm_token, account,
                                          self.services.auth_url, profile=self.profile),
                                      headers=XML_HEADERS)
        validate_alarm_response(payload)
        self._registered = True

    async def async_subscribe(self, device: QvCloudDevice, channel: int, enabled=True):
        account = self._require_account()
        if not self._registered:
            raise QvCloudError('not_registered')
        if not isinstance(device, QvCloudDevice) or self._devices.get(device.uid) != device:
            raise QvCloudError('unauthorized_device')
        body = build_subscription_request(account.session_id, device, channel, enabled)
        payload = await self._request('alarm', 'POST', self.services.alarm_url,
                                      ALARM_PATH, data=body,
                                      headers=XML_HEADERS)
        validate_alarm_response(payload)

    async def _finish_close(self):
        """Keep ownership of cleanup when a flow/unload caller is canceled."""
        poll, self._poll_task = self._poll_task, None
        if poll is not None:
            poll.cancel()

        async def close_session(session):
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT):
                    await session.close()
            except Exception:
                # Cleanup is best effort and must not expose transport errors.
                pass

        async def join_poll():
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT):
                    await asyncio.gather(poll, return_exceptions=True)
            except TimeoutError:
                pass

        try:
            operations = [close_session(session) for session in self._sessions.values()]
            if poll is not None:
                operations.append(join_poll())
            await asyncio.gather(*operations)
        finally:
            self._sessions.clear()
            self._poll_ready.clear()
            self._poll_error = None

    async def async_close(self):
        """Clear secrets immediately; finish bounded cleanup despite cancellation."""
        if self._close_task is None:
            self._closed = True
            self._account = self._email = self._digest = self._oauth = None
            self._devices.clear()
            self._registered = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(QvCloudError('closed'))
            self._poll_ready.set()
            self._close_task = asyncio.create_task(self._finish_close(),
                                                  name='welcomeeye-qv-close')
        await asyncio.shield(self._close_task)
