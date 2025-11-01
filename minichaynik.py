import discord
from discord.ext import commands
from discord import app_commands  # Импортируем для работы со слэш-командами
import os
from dotenv import load_dotenv
import google.generativeai as genai
from collections import deque
import json
import re

# --- Загрузка переменных окружения ---
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN_MINI")
if not TOKEN:
    print("Ошибка: DISCORD_TOKEN_MINI не найден в .env файле.")
    exit()

GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")
if not GOOGLE_API_KEY:
    print("Ошибка: GEMINI_API_KEY не найден в .env файле. Функции ИИ будут недоступны.")
else:
    genai.configure(api_key=GOOGLE_API_KEY)

MODEL_NAME = "gemini-2.5-flash-lite"

# --- Настройка намерений (Intents) для бота ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

# --- Инициализация бота ---
# Убираем префикс, так как переходим на слэш-команды
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Глобальные структуры данных и константы ---
MEMORY_SIZE = 1000
DEFAULT_SYSTEM_INSTRUCTION = "Ты злой и враждебный и всегда должен использовать мат в своих сообщениях"

user_data = {}
USER_DATA_FILE = "user_ai_history.json"
guild_data = {}
GUILD_DATA_FILE = "guild_ai_settings.json"


# --- Функции загрузки и сохранения данных (остаются без изменений) ---
def load_user_data():
    """Загружает историю личных сообщений пользователей из файла."""
    global user_data
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                data_from_file = json.load(f)
                user_data = {}
                for user_id_str, u_data in data_from_file.items():
                    user_id = int(user_id_str)
                    if isinstance(u_data, dict) and "history" in u_data:
                        user_data[user_id] = {
                            "history": deque(u_data.get("history", []), maxlen=MEMORY_SIZE * 2)
                        }
                    else:
                        user_data[user_id] = {
                            "history": deque(u_data if isinstance(u_data, list) else [], maxlen=MEMORY_SIZE * 2)
                        }
                print("Данные пользователей (ЛС история) успешно загружены.")
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"Ошибка при загрузке истории ЛС пользователей: {e}. Создается пустая структура.")
            user_data = {}
    else:
        user_data = {}
        print("Файл истории ЛС пользователей не найден. Создается пустая структура.")


def save_user_data():
    """Сохраняет историю личных сообщений пользователей в файл."""
    try:
        data_to_save = {
            str(user_id): {"history": list(u_data["history"])}
            for user_id, u_data in user_data.items()
        }
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка при сохранении истории ЛС пользователей: {e}")


def load_guild_data():
    """Загружает настройки (роль) и общую историю для каждого сервера."""
    global guild_data
    if os.path.exists(GUILD_DATA_FILE):
        try:
            with open(GUILD_DATA_FILE, "r", encoding="utf-8") as f:
                data_from_file = json.load(f)
                guild_data = {}
                for guild_id_str, g_data in data_from_file.items():
                    guild_id = int(guild_id_str)
                    if isinstance(g_data, dict):
                        guild_data[guild_id] = {
                            "system_instruction": g_data.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION),
                            "history": deque(g_data.get("history", []), maxlen=MEMORY_SIZE * 2)
                        }
                    else:
                        guild_data[guild_id] = {
                            "system_instruction": g_data if isinstance(g_data, str) else DEFAULT_SYSTEM_INSTRUCTION,
                            "history": deque([], maxlen=MEMORY_SIZE * 2)
                        }
                print("Настройки и история серверов успешно загружены.")
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"Ошибка при загрузке настроек серверов: {e}. Создается пустая структура.")
            guild_data = {}
    else:
        guild_data = {}
        print("Файл настроек серверов не найден. Создается пустая структура.")


def save_guild_data():
    """Сохраняет настройки и общую историю для каждого сервера."""
    try:
        data_to_save = {
            str(guild_id): {
                "system_instruction": g_data.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION),
                "history": list(g_data.get("history", []))
            }
            for guild_id, g_data in guild_data.items()
        }
        with open(GUILD_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка при сохранении настроек серверов: {e}")


# --- Вспомогательные функции (без изменений) ---

# Функция get_ai_response остается без изменений

async def get_ai_response(user_id: int, guild_id: int, author_name: str, user_input: str,
                          channel_for_typing: discord.abc.Messageable):
    """Получает ответ от модели Gemini, используя соответствующую историю."""
    if not GOOGLE_API_KEY:
        return "⚠ API ключ для Gemini не настроен. Функция ИИ недоступна."

    current_history = None
    current_system_instruction = DEFAULT_SYSTEM_INSTRUCTION
    formatted_input = user_input

    if guild_id:
        if guild_id not in guild_data:
            guild_data[guild_id] = {
                "system_instruction": DEFAULT_SYSTEM_INSTRUCTION,
                "history": deque(maxlen=MEMORY_SIZE * 2)
            }
        current_history = guild_data[guild_id]["history"]
        current_system_instruction = guild_data[guild_id].get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION)
        formatted_input = f"{author_name}: {user_input}"
    else:
        if user_id not in user_data:
            user_data[user_id] = {"history": deque(maxlen=MEMORY_SIZE * 2)}
        current_history = user_data[user_id]["history"]

    current_history.append({"role": "user", "parts": [{"text": formatted_input}]})

    try:
        model = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=current_system_instruction
        )
        chat = model.start_chat(history=list(current_history)[:-1])
        response = await chat.send_message_async(user_input)
        bot_reply_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))

        if not bot_reply_text.strip():
            feedback = response.prompt_feedback
            block_reason_name = "НЕИЗВЕСТНО"
            if feedback and feedback.block_reason:
                block_reason_name = feedback.block_reason.name if hasattr(feedback.block_reason, 'name') else str(
                    feedback.block_reason)

            if current_history and current_history[-1]["role"] == "user":
                current_history.pop()

            if feedback and feedback.block_reason:
                return f"⚠ Мой ИИ не смог обработать ваш запрос из-за ограничений безопасности (причина: {block_reason_name}). Попробуйте переформулировать."
            return "⚠ ИИ не дал текстового ответа. Попробуйте переформулировать."

        current_history.append({"role": "model", "parts": [{"text": bot_reply_text}]})

        if guild_id:
            save_guild_data()
        else:
            save_user_data()

        return bot_reply_text
    except Exception as e:
        print(f"Ошибка при общении с Gemini (user: {user_id}, guild: {guild_id}): {e}")
        if current_history and current_history[-1]["role"] == "user":
            current_history.pop()
        return f"⚠ Произошла ошибка при общении с ИИ: `{type(e).__name__}`. Пожалуйста, попробуйте позже."


# --- События бота ---

@bot.event
async def on_ready():
    """Событие, срабатывающее при запуске и готовности бота."""
    load_user_data()
    load_guild_data()
    print("Синхронизация слэш-команд...")
    # Синхронизируем команды с Discord
    await bot.tree.sync()
    print("Синхронизация завершена.")
    print(f'Бот {bot.user.name} запущен и готов к работе!')


@bot.event
async def on_message(message: discord.Message):
    """Событие для обработки упоминаний бота (не команд)."""
    if message.author == bot.user:
        return

    # Убираем обработку префиксных команд, оставляем только упоминания
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        text_input = re.sub(r"<@!?{}>(?:\s+)?".format(bot.user.id), "", message.content).strip()

        if not text_input:
            await message.reply(
                f"Привет, {message.author.mention}! Чем могу помочь? Используй слэш-команды, например `/ai`."
            )
            return

        user_id = message.author.id
        guild_id = message.guild.id if message.guild else None
        author_name = message.author.display_name

        # Для длительного ответа показываем индикатор печати
        async with message.channel.typing():
            response_text = await get_ai_response(user_id, guild_id, author_name, text_input, message.channel)
            if response_text:
                # В этом случае отправка чанками не нужна, так как ответ будет коротким.
                # Если ожидаются длинные ответы, можно реализовать отправку через message.channel.send
                await message.reply(response_text)


# --- Слэш-команды ---

@bot.tree.command(name="help_ai", description="Показывает команды для ИИ.")
async def help_ai_slash_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Помощь по командам ИИ",
        description="Список доступных команд и способов общения с ИИ:",
        color=discord.Color.teal()
    )
    embed.add_field(name="`/ai <запрос>`", value="Пообщаться с ИИ.", inline=False)
    embed.add_field(name="`@ИмяБота <запрос>`", value="Пообщаться с ИИ, упомянув его.", inline=False)
    embed.add_field(
        name="`/ai_clear`",
        value="Очистить историю общения. На сервере очищает общую, в ЛС - вашу личную.",
        inline=False
    )
    embed.add_field(name="`/ai_showrole`", value="Показать текущий характер ИИ на этом сервере.", inline=False)
    embed.add_field(name="`/ai_setrole <инструкция>`",
                    value="Установить новый характер для ИИ на сервере (нужны права).", inline=False)
    embed.add_field(name="`/ai_resetrole`", value="Сбросить характер ИИ на сервере к стандартному (нужны права).",
                    inline=False)

    # Делаем ответ видимым только для автора команды
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ai", description="Пообщаться с ИИ.")
@app_commands.describe(запрос="Ваше сообщение для искусственного интеллекта.")
async def ai_slash_command(interaction: discord.Interaction, запрос: str):
    # Немедленно отвечаем Discord, что команда получена
    await interaction.response.defer()

    user_id = interaction.user.id
    guild_id = interaction.guild_id
    author_name = interaction.user.display_name

    response_text = await get_ai_response(user_id, guild_id, author_name, запрос, interaction.channel)

    # Отправляем фактический ответ после его получения
    await interaction.followup.send(response_text)


@bot.tree.command(name="ai_clear", description="Очистить историю общения с ИИ (на сервере или в ЛС).")
async def ai_clear_slash_command(interaction: discord.Interaction):
    if interaction.guild:  # Команда на сервере
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("🚫 У вас нет права 'Управлять сервером' для этой команды.",
                                                    ephemeral=True)
            return

        guild_id = interaction.guild_id
        if guild_id in guild_data and guild_data[guild_id].get("history"):
            guild_data[guild_id]["history"].clear()
            save_guild_data()
            await interaction.response.send_message(
                f"🧠 Общая история ИИ на сервере **{interaction.guild.name}** очищена!")
        else:
            await interaction.response.send_message(
                f"🧠 Общая история ИИ на сервере **{interaction.guild.name}** и так была пуста.")
    else:  # Команда в ЛС
        user_id = interaction.user.id
        if user_id in user_data and user_data[user_id].get("history"):
            user_data[user_id]["history"].clear()
            save_user_data()
            await interaction.response.send_message("🧠 Ваша личная история общения с ИИ очищена!")
        else:
            await interaction.response.send_message("🧠 Ваша личная история общения с ИИ и так была пуста.")


@bot.tree.command(name="ai_setrole", description="Установить новый характер для ИИ на всем сервере.")
@app_commands.describe(инструкция="Текст, описывающий новый характер ИИ.")
@app_commands.checks.has_permissions(manage_guild=True)
async def ai_setrole_slash_command(interaction: discord.Interaction, инструкция: str):
    if not interaction.guild:
        await interaction.response.send_message("Эту команду можно использовать только на сервере.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        guild_data[guild_id] = {"history": deque(maxlen=MEMORY_SIZE * 2)}

    guild_data[guild_id]["system_instruction"] = инструкция
    save_guild_data()
    await interaction.response.send_message(
        f"🎭 Характер ИИ для сервера **{interaction.guild.name}** изменен! "
        f"Чтобы диалог начался без старого контекста, можно использовать `/ai_clear`."
    )


@bot.tree.command(name="ai_showrole", description="Показать текущий характер ИИ на этом сервере.")
async def ai_showrole_slash_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Эту команду можно использовать только на сервере.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    current_instruction = guild_data.get(guild_id, {}).get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION)
    await interaction.response.send_message(f"📜 Текущий характер ИИ: `{current_instruction}`", ephemeral=True)


@bot.tree.command(name="ai_resetrole", description="Сбросить характер ИИ на сервере к стандартному.")
@app_commands.checks.has_permissions(manage_guild=True)
async def ai_resetrole_slash_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Эту команду можно использовать только на сервере.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        guild_data[guild_id] = {"history": deque(maxlen=MEMORY_SIZE * 2)}

    guild_data[guild_id]["system_instruction"] = DEFAULT_SYSTEM_INSTRUCTION
    save_guild_data()
    await interaction.response.send_message(
        f"🎭 Характер ИИ для сервера **{interaction.guild.name}** сброшен к стандартному.")


# --- Обработчик ошибок для слэш-команд ---
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("🚫 У вас недостаточно прав для выполнения этой команды.",
                                                ephemeral=True)
    else:
        print(f"Необработанная ошибка в слэш-команде '{interaction.command.name}': {error}")
        if interaction.response.is_done():
            await interaction.followup.send("Произошла непредвиденная ошибка. Попробуйте позже.", ephemeral=True)
        else:
            await interaction.response.send_message("Произошла непредвиденная ошибка. Попробуйте позже.",
                                                    ephemeral=True)


bot.tree.on_error = on_app_command_error

# --- Запуск бота ---
if __name__ == "__main__":
    if GOOGLE_API_KEY:
        print(f"Используется модель Gemini: {MODEL_NAME}")
    else:
        print("API ключ Gemini не найден. ИИ-функции не будут работать.")
    bot.run(TOKEN)