import os

import asyncio

import telegram

# bot_id=6608056833
async def main():
    bot = telegram.Bot(os.getenv("CHAIN_HEALTH_TGBOT_TOKEN"))
    async with bot:
        print(await bot.get_me())


if __name__ == '__main__':
    asyncio.run(main())
