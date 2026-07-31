"""Alexa Smart Home Nexus GraphQL client."""

from __future__ import annotations

from http import HTTPMethod
from typing import Any

from aioamazondevices.const.http import URI_NEXUS_GRAPHQL
from aioamazondevices.exceptions import CannotAuthenticate, CannotConnect, CannotRetrieveData
from aioamazondevices.http_wrapper import AmazonHttpWrapper, AmazonSessionStateData
from aioamazondevices.login import AmazonLogin
from aiohttp import ClientSession
from yarl import URL

from .models import AlexaApiError, CapabilityState
from .queries import (
    MUTATION_SET_ENDPOINT_FEATURES,
    QUERY_FAN_STATE,
    QUERY_SMART_HOME_ENDPOINTS,
)


class AlexaSmartHomeClient:
    """Client for Alexa Smart Home appliance GraphQL APIs."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        login_data: dict[str, Any],
    ) -> None:
        """Initialize the client with stored Amazon login data."""
        site = login_data.get("site", "https://www.amazon.com")
        self._session_state = AmazonSessionStateData(
            site, username, password, login_data
        )
        self._http = AmazonHttpWrapper(session, self._session_state)
        self._login = AmazonLogin(
            http_wrapper=self._http,
            session_state_data=self._session_state,
        )
        self._logged_in = False

    async def async_login_stored(self) -> None:
        """Authenticate using stored login data."""
        await self._login.login_mode_stored_data()
        self._logged_in = True

    async def _ensure_login(self) -> None:
        """Ensure stored login has been applied before requests."""
        if not self._logged_in:
            await self.async_login_stored()

    async def _graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """POST a GraphQL query/mutation to Alexa Nexus."""
        await self._ensure_login()
        try:
            _, resp = await self._http.session_request(
                method=HTTPMethod.POST,
                url=URL.joinpath(self._session_state.alexa_website_url, URI_NEXUS_GRAPHQL),
                input_data={"query": query, "variables": variables or {}},
                json_data=True,
            )
            payload = await self._http.response_to_json(resp, "nexus_graphql")
        except (CannotAuthenticate, CannotConnect, CannotRetrieveData):
            raise
        except Exception:
            # Contingency: csrf cookie may be missing; prime via website GET then retry once.
            try:
                await self._http.session_request(
                    method=HTTPMethod.GET,
                    url=self._session_state.alexa_website_url,
                )
                _, resp = await self._http.session_request(
                    method=HTTPMethod.POST,
                    url=URL.joinpath(
                        self._session_state.alexa_website_url, URI_NEXUS_GRAPHQL
                    ),
                    input_data={"query": query, "variables": variables or {}},
                    json_data=True,
                )
                payload = await self._http.response_to_json(resp, "nexus_graphql")
            except (CannotAuthenticate, CannotConnect, CannotRetrieveData):
                raise
            except Exception as retry_err:
                raise AlexaApiError(
                    f"GraphQL request failed: {retry_err}"
                ) from retry_err
        if payload.get("errors"):
            error = payload["errors"][0]
            message = error.get("message", "Unknown GraphQL error")
            raise AlexaApiError(message, code=error.get("extensions", {}).get("code"))

        data = payload.get("data")
        if not isinstance(data, dict):
            raise AlexaApiError("GraphQL response missing data")
        return data

    async def async_get_endpoints(self) -> list[dict[str, Any]]:
        """Fetch Smart Home endpoints including legacyAppliance capabilities."""
        data = await self._graphql(QUERY_SMART_HOME_ENDPOINTS)
        items = ((data.get("endpoints") or {}).get("items")) or []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def async_get_fan_state(self, endpoint_id: str) -> list[CapabilityState]:
        """Fetch power/mode/range state for a fan endpoint."""
        data = await self._graphql(QUERY_FAN_STATE, {"endpointId": endpoint_id})
        endpoint = data.get("endpoint") or {}
        features = endpoint.get("features") or []
        states: list[CapabilityState] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            power_state: str | None = None
            mode_value: str | None = None
            range_value: float | None = None
            for prop in feature.get("properties") or []:
                if not isinstance(prop, dict):
                    continue
                if "powerStateValue" in prop and prop["powerStateValue"] is not None:
                    power_state = str(prop["powerStateValue"])
                mode = prop.get("modeValue") or {}
                if isinstance(mode, dict) and mode.get("value") is not None:
                    mode_value = str(mode["value"])
                range_obj = prop.get("rangeValue") or {}
                if isinstance(range_obj, dict) and range_obj.get("value") is not None:
                    range_value = float(range_obj["value"])
            states.append(
                CapabilityState(
                    name=str(feature.get("name") or ""),
                    instance=feature.get("instance"),
                    power_state=power_state,
                    mode_value=mode_value,
                    range_value=range_value,
                )
            )
        return states

    async def _async_set_endpoint_feature(self, request: dict[str, Any]) -> None:
        """Execute setEndpointFeatures and raise on Amazon errors."""
        data = await self._graphql(
            MUTATION_SET_ENDPOINT_FEATURES,
            {"featureControlRequests": [request]},
        )
        result = data.get("setEndpointFeatures") or {}
        errors = result.get("errors") or []
        if errors:
            error = errors[0]
            code = error.get("code")
            raise AlexaApiError(
                f"setEndpointFeatures failed for {error.get('endpointId')}: {code}",
                code=code,
            )

    async def async_set_power(self, endpoint_id: str, turn_on: bool) -> None:
        """Turn a fan endpoint on or off."""
        await self._async_set_endpoint_feature(
            {
                "endpointId": endpoint_id,
                "featureName": "power",
                "featureOperationName": "turnOn" if turn_on else "turnOff",
                "payload": {},
            }
        )

    async def async_set_mode(
        self, endpoint_id: str, instance: str, mode: str
    ) -> None:
        """Set a ModeController value on a fan endpoint."""
        await self._async_set_endpoint_feature(
            {
                "endpointId": endpoint_id,
                "featureName": "mode",
                "featureOperationName": "setMode",
                "instance": instance,
                "payload": {"mode": mode},
            }
        )
