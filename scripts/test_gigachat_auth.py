# scripts/test_gigachat_auth.py
"""
Тест авторизации GigaChat API
Запуск: python -m scripts.test_gigachat_auth
"""
import asyncio
import httpx
from modules.llm.config import llm_config


async def test_auth():
    print("Тест авторизации GigaChat API")
    print(f"Authorization Key: {llm_config.gigachat_authorization_key[:20]}...")
    print(f"Scope: {llm_config.gigachat_scope}")

    auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    # Используем Authorization Key напрямую
    headers = {
        'Authorization': f'Basic {llm_config.gigachat_authorization_key}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': '6f0b1291-c7f3-43c6-bb2e-9f3efb2dc98e'
    }

    data = {'scope': llm_config.gigachat_scope}

    print(f"\nAuthorization Header: Basic {llm_config.gigachat_authorization_key[:30]}...")
    print(f"\nЗапрос к {auth_url}")

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        response = await client.post(auth_url, headers=headers, data=data)

        print(f"\nСтатус: {response.status_code}")
        print(f"Тело ответа: {response.text[:500]}")

        if response.status_code == 200:
            token_data = response.json()
            print(f"\nУСПЕХ!")
            print(f"Access Token: {token_data['access_token'][:30]}...")
            print(f"Expires At: {token_data['expires_at']}")
        else:
            print(f"\nОШИБКА!")
            print(f"\nВозможные причины:")
            print(f"   1. Authorization Key неверный или истёк")
            print(f"   2. Проект не активирован в личном кабинете")
            print(f"   3. Scope неверный")
            print(f"\nПопробуйте обновить Authorization Key в ЛК Sber")


if __name__ == "__main__":
    asyncio.run(test_auth())
