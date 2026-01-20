import asyncio

from telethon import TelegramClient

from telebot.interfaces.cli.main import Settings


async def main():
    settings = Settings()
    client = TelegramClient("telebot", settings.tg_api_id, settings.tg_api_hash)
    await client.start(phone=settings.phone_number)
    async for dialog in client.iter_dialogs():
        if "Course" in dialog.name:
            username = dialog.entity.username if hasattr(dialog.entity, "username") else "None"
            print(f"Name: {dialog.name} | ID: {dialog.id} | Username: {username}")


if __name__ == "__main__":
    asyncio.run(main())
