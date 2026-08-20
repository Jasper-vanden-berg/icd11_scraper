import requests
import logging
import httpx
def get_token(api_settings):
    payload = {
        "client_id": api_settings.get("client_id"),
        "client_secret": api_settings.get("client_secret"),
        "scope": api_settings.get("scope"),
        "grant_type": api_settings.get("grant_type"),
    }

    r = requests.post(
        "https://icdaccessmanagement.who.int/connect/token",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    r.raise_for_status()

    return r.json()["access_token"]


def get_latest_release(api_settings, token: str | None):
    if not token:
        token = get_token(api_settings)

    url = api_settings.get("entity_url")
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "API-Version": "v2",
            "Accept-Language": "en",
        }
    )

    r.raise_for_status()

    return r.json().get("releaseId")

async def fetch_json(client, url, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "API-Version": "v2",
        "Accept-Language": "en",
    }

    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    except (httpx.RequestError, httpx.HTTPStatusError):
        return None