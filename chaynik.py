import asyncio
import discord
from discord.ext import commands
import os
import subprocess
from dotenv import load_dotenv
import google.generativeai as genai
from collections import deque
import json
import aiohttp
from io import BytesIO
from g4f.client import Client
import yt_dlp
import requests
import tempfile
from PIL import Image
from google.generativeai import GenerativeModel
import edge_tts
import re
import logging
import time
import threading  # ИМПОРТИРУЕМ threading

# --- НОВЫЙ КОД ---
# Словарь для хранения событий отмены скачивания для каждого сервера
download_cancellation_events = {}


# Пользовательское исключение для прерывания скачивания
class DownloadCancelled(Exception):
    pass


# --- КОНЕЦ НОВОГО КОДА ---

# ИСПРАВЛЕНИЕ: Добавляем замок для контроля одновременных загрузок
download_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)
MODEL = "gemini-2.5-flash-lite"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
chaynik_file = "chaynik.wav"
ffmpeg = "ffmpeg/ffmpeg"
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mkv", ".avi", ".mov"}

MEMORY_SIZE = 1000
MEMORY_FILE = "memory.json"
memory = {}

queues = {}
current_song_data = {}

voice_chat_history = {}
client = Client()
voice = "ru-RU-DmitryNeural"


def full_cleanup(guild_id):
    print(f"Запускаю полную очистку для сервера {guild_id}...")
    if guild_id in current_song_data and current_song_data.get(guild_id):
        current_file = current_song_data[guild_id].get('file')
        if current_file and os.path.exists(current_file):
            try:
                os.remove(current_file)
                print(f"Файл текущей песни '{current_file}' удален при очистке.")
            except Exception as e:
                print(f"Ошибка при удалении файла текущей песни '{current_file}': {e}")

    if guild_id in queues and queues.get(guild_id):
        for song in queues[guild_id]:
            queued_file = song.get('file')
            if queued_file and os.path.exists(queued_file):
                try:
                    os.remove(queued_file)
                    print(f"Файл из очереди '{queued_file}' удален при очистке.")
                except Exception as e:
                    print(f"Ошибка при удалении файла из очереди '{queued_file}': {e}")

    queues.pop(guild_id, None)
    current_song_data.pop(guild_id, None)
    print(f"Полная очистка для сервера {guild_id} завершена.")


@bot.event
async def on_ready():
    load_memory()
    print(f"✅ Бот {bot.user} запущен и готов к работе!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        if before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id
            # --- НОВЫЙ КОД ---
            # Сигнализируем о необходимости отмены скачивания
            if guild_id in download_cancellation_events:
                download_cancellation_events[guild_id].set()
                print(f"Установлен флаг отмены скачивания для сервера {guild_id}.")
            # --- КОНЕЦ НОВОГО КОДА ---
            full_cleanup(guild_id)
            return

    if before.channel != after.channel:
        if after.channel:
            if after.channel.guild.id not in voice_chat_history:
                voice_chat_history[after.channel.guild.id] = {}
            if after.channel.id not in voice_chat_history[after.channel.guild.id]:
                voice_chat_history[after.channel.guild.id][after.channel.id] = {}
            if member.id not in voice_chat_history[after.channel.guild.id][after.channel.id]:
                voice_chat_history[after.channel.guild.id][after.channel.id][member.id] = []
            if after.channel.guild.system_channel:
                await after.channel.guild.system_channel.send(
                    f'"{member.nick or member.name}" ({member}) подключился к голосовому каналу {after.channel.name}!')
        elif before.channel:
            if before.channel.guild.id in voice_chat_history and before.channel.id in voice_chat_history[
                before.channel.guild.id] and member.id in voice_chat_history[before.channel.guild.id][
                before.channel.id]:
                voice_chat_history[before.channel.guild.id][before.channel.id].pop(member.id, None)
            if before.channel.guild.system_channel:
                await before.channel.guild.system_channel.send(
                    f'"{member.nick or member.name}" ({member}) отключился от голосового канала {before.channel.name}!')


@bot.tree.command(name="help", description="Показывает список доступных команд")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Помощь по командам", description="Список доступных команд:",
                          color=discord.Color.blue())
    embed.add_field(name="/help", value="Показать это сообщение", inline=False)
    embed.add_field(name="!ai <запрос>",
                    value="Пообщаться с умным ИИ. Может описывать изображения (прикрепите файл или вставьте ссылку).",
                    inline=False)
    embed.add_field(name="!ai_clear", value="Стереть память ИИ", inline=False)
    embed.add_field(name="!speak <запрос>", value="Поговорить с умным ИИ в голосовом чате", inline=False)
    embed.add_field(name="!say <запрос>", value="Сказать что-то с ИИ в голосовом чате", inline=False)
    embed.add_field(name="!image <запрос>", value="Сгенерировать изображение", inline=False)
    embed.add_field(name="!play <файл/ссылка/запрос>", value="Включить звук (добавляет в очередь)", inline=False)
    embed.add_field(name="!queue", value="Показать текущую очередь песен", inline=False)
    embed.add_field(name="!chaynik", value="Включить чайник", inline=False)
    embed.add_field(name="!vikini", value="Пропустить текущую песню", inline=False)
    embed.add_field(name="!viydi", value="Выкинуть бота из голосового канала", inline=False)
    embed.set_footer(text="Чайник Бот | Лучший бот в мире!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command(name="chaynik", help="Включить чайник")
async def chaynik(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            await channel.connect()
        elif ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)
    else:
        await ctx.reply("Пидор, зайди в голосовой канал!")
        return

    if not ctx.voice_client.is_playing():
        try:
            audio_source = discord.FFmpegPCMAudio(chaynik_file, executable=ffmpeg)
            ctx.voice_client.play(audio_source)
            await ctx.reply("Пидор, чайник включен")
        except Exception as e:
            await ctx.reply(f"Пидор, ошибка при воспроизведении чайника: {e}")
    else:
        await ctx.reply("Пидор, что-то уже играет")


def extract_audio_from_video(file_path):
    audio_path = f"temp_audio_{os.path.splitext(os.path.basename(file_path))[0]}.mp3"
    result = subprocess.run(
        [ffmpeg, "-i", file_path, "-vn", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", "-ab", "192k",
         audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise Exception(f"Пидор, ошибка при извлечении аудио! {result.stderr}")
    return audio_path


async def process_file(ctx, attachment):
    file_ext = os.path.splitext(attachment.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        await ctx.reply(f"Пидор, поддерживаются только {', '.join(ALLOWED_EXTENSIONS)} файлы!")
        return None

    temp_file_path = f"temp_{attachment.filename}"
    try:
        await attachment.save(temp_file_path)
        if file_ext in {".mp4", ".mkv", ".avi", ".mov"}:
            audio_path = extract_audio_from_video(temp_file_path)
            os.remove(temp_file_path)
            return audio_path
        else:
            return temp_file_path
    except Exception as e:
        await ctx.reply(f"Пидор, ошибка при обработке файла: {e}")
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return None


def load_memory():
    global memory
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                memory = {int(k): deque(v, maxlen=MEMORY_SIZE) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Ошибка при загрузке памяти: {e}. Создаем пустую память.")
            memory = {}
    else:
        memory = {}


def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in memory.items()}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка при сохранении памяти: {e}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    user_id = message.author.id
    if user_id not in memory:
        memory[user_id] = deque(maxlen=MEMORY_SIZE)
    # Сохраняем в память только если это не команда !ai, чтобы избежать дублирования
    if not message.content.startswith("!ai"):
        memory[user_id].append({"role": "user", "parts": [message.content]})

    if bot.user in message.mentions:
        await message.channel.typing()
        try:
            model = genai.GenerativeModel(MODEL)
            chat = model.start_chat(history=list(memory[user_id]))
            response = chat.send_message(message.content)
            bot_reply = response.text
            memory[user_id].append({"role": "model", "parts": [bot_reply]})
            save_memory()
            await send_message_in_chunks(message, bot_reply)
        except Exception as e:
            await message.reply(f"⚠ Ошибка при общении с Gemini: {e}")
    if "писюн" in message.content.lower():
        await message.reply("Выключи его нахуй!!!!!")
        await message.add_reaction("😈")
    member_shap = message.guild.get_member(1030829712467034112)
    if member_shap and (
            "шап" in message.content.lower() or "пипунап" in message.content.lower() or "белый пипидастр" in message.content.lower()):
        await message.reply(f"Мистер шап - писюнап!!! {member_shap.mention}")
    if any(phrase in message.content.lower() for phrase in
           ["кто в гс", "го в гс", "го играть", "кто играть", "кто пойдет в гс"]):
        await message.channel.send(f"@everyone {message.content}")
    await bot.process_commands(message)


def find_url(text):
    if not text:
        return None
    # Простой regex для поиска URL
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
    return urls[0] if urls else None


@bot.command()
async def ai(ctx, *, user_input: str = None):
    user_id = ctx.author.id
    temp_image_path = None
    image_to_process = None
    prompt_text = user_input or "Опиши это изображение."

    try:
        await ctx.channel.typing()

        # 1. Проверка на прикрепленные файлы
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                await ctx.reply("Если прикрепляешь файл, это должно быть изображение (png, jpg, jpeg, gif).")
                return

            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(attachment.filename)[1]) as tmp_file:
                await attachment.save(tmp_file.name)
                temp_image_path = tmp_file.name
            image_to_process = Image.open(temp_image_path)

        # 2. Если нет вложений, ищем URL в тексте
        elif user_input:
            url = find_url(user_input)
            if url:
                prompt_text = user_input.replace(url, "").strip() or "Опиши это изображение."
                response = requests.get(url, stream=True)
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                    tmp_file.write(response.content)
                    temp_image_path = tmp_file.name
                image_to_process = Image.open(temp_image_path)

        # --- Выполнение логики ---

        # Если было найдено изображение (из файла или URL)
        if image_to_process:
            model = genai.GenerativeModel(MODEL)
            # Запрос состоит из текста пользователя и изображения
            response = model.generate_content([prompt_text, image_to_process], stream=False)

            # --- ИСПРАВЛЕНИЕ: Закрываем файл перед удалением ---
            image_to_process.close()
            # ----------------------------------------------------

            bot_reply = response.text
            # Разговоры с изображениями пока не сохраняем в общую память,
            # чтобы не усложнять структуру.
            await send_message_in_chunks(ctx, bot_reply)

        # Если это чисто текстовый запрос
        elif user_input:
            if user_id not in memory:
                memory[user_id] = deque(maxlen=MEMORY_SIZE)
            memory[user_id].append({"role": "user", "parts": [user_input]})

            model = genai.GenerativeModel(MODEL)
            chat = model.start_chat(history=list(memory[user_id]))
            response = chat.send_message(user_input)
            bot_reply = response.text
            memory[user_id].append({"role": "model", "parts": [bot_reply]})
            save_memory()
            await send_message_in_chunks(ctx, bot_reply)

        # Если вообще ничего не было введено
        else:
            await ctx.reply("Пожалуйста, введи текстовый запрос, прикрепи изображение или дай ссылку на него.")

    except Exception as e:
        await ctx.reply(f"⚠ Произошла ошибка: {e}")
    finally:
        # Очистка временного файла изображения, если он был создан
        if temp_image_path and os.path.exists(temp_image_path):
            # Дополнительно убеждаемся, что объект image_to_process закрыт, если он существует
            if image_to_process:
                try:
                    image_to_process.close()
                except Exception:
                    pass  # Игнорируем ошибки, если он уже закрыт
            os.remove(temp_image_path)


async def send_message_in_chunks(ctx, text):
    for i in range(0, len(text), 1800):
        await ctx.reply(text[i:i + 1800])


@bot.command(name="ai_clear", help="Очистить память бота")
async def ai_clear(ctx):
    user_id = ctx.author.id
    if user_id in memory:
        del memory[user_id]
        save_memory()
    await ctx.reply("🧠 Моя память очищена!")


@bot.command(name="image", help="Сгенерировать изображение")
async def generate_image(ctx, *, prompt: str = None):
    if not prompt:
        await ctx.reply("Введите запрос!")
        return
    await ctx.typing()
    try:
        response = await asyncio.to_thread(client.images.generate, model="flux", prompt=prompt)
        image_url = response.data[0].url
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    image_file = discord.File(BytesIO(image_data), filename="generated_image.png")
                    await ctx.reply(file=image_file)
                else:
                    await ctx.reply("Не удалось скачать сгенерированное изображение.")
    except Exception as e:
        await ctx.reply(f"Произошла ошибка при генерации изображения: {e}")


ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': 'temp_%(id)s_%(epoch)s.%(ext)s',
    'noplaylist': True,
    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
    'ffmpeg_location': ffmpeg,
    'quiet': True,
    'no_warnings': True,
}


def search_youtube(query, max_results=1):
    search_query = f'ytsearch{max_results}:{query}'
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(search_query, download=False)
            return result.get('entries', [])
        except Exception as e:
            logging.error(f"Ошибка при поиске на YouTube: {e}")
            return []


# --- ИЗМЕНЕННАЯ ФУНКЦИЯ ---
def download_audio(video_url, cancellation_event):
    # Функция-хук, которая будет вызываться в процессе скачивания
    def progress_hook(d):
        if cancellation_event.is_set():
            raise DownloadCancelled("Скачивание отменено, так как бот покинул канал.")

    # Копируем опции и добавляем наш хук
    local_ydl_opts = ydl_opts.copy()
    local_ydl_opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(local_ydl_opts) as ydl:
            logging.info(f"Начинаю обработку URL: {video_url}")
            info = ydl.extract_info(video_url, download=True)
            title = info.get('title', 'Без названия')
            base_filename = ydl.prepare_filename(info).rsplit('.', 1)[0]
            audio_file = f"{base_filename}.mp3"

            # Небольшая задержка, чтобы файл успел полностью записаться
            time.sleep(0.5)

            if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                return audio_file, title
            return None, None
    except DownloadCancelled as e:
        logging.warning(e)  # Логируем, что скачивание было отменено
        return None, None
    except Exception as e:
        logging.error(f"Ошибка при скачивании аудио: {e}", exc_info=True)
        return None, None


# --- КОНЕЦ ИЗМЕНЕННОЙ ФУНКЦИИ ---

async def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in current_song_data and current_song_data[guild_id]:
        old_data = current_song_data[guild_id]
        old_source = old_data.get('source')
        old_file = old_data.get('file')
        if old_source:
            old_source.cleanup()
        await asyncio.sleep(0.5)
        if old_file and os.path.exists(old_file):
            try:
                os.remove(old_file)
                print(f"Временный файл '{old_file}' успешно удален.")
            except Exception as e:
                print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось удалить старый файл '{old_file}': {e}")
    current_song_data[guild_id] = None

    if guild_id in queues and queues[guild_id]:
        song_to_play = queues[guild_id].pop(0)
        file_path, title = song_to_play['file'], song_to_play['title']
        if not os.path.exists(file_path):
            await ctx.send(f"Ошибка: аудиофайл для '{title}' не найден. Пропускаю.")
            await play_next(ctx)
            return
        await ctx.send(f"Играю гамно: {title}!")
        new_source = discord.FFmpegPCMAudio(file_path, executable=ffmpeg)
        current_song_data[guild_id] = {'file': file_path, 'source': new_source, 'title': title}
        ctx.voice_client.play(new_source, after=lambda e: bot.loop.create_task(play_next(ctx)))
    else:
        await ctx.send("Очередь воспроизведения завершена.")
        current_song_data[guild_id] = None


# --- ПОЛНОСТЬЮ ПЕРЕПИСАННАЯ КОМАНДА ---
@bot.command(name="play", help="Воспроизводит аудио или добавляет в очередь.")
async def play(ctx, *, query: str = None):
    if not ctx.author.voice:
        await ctx.reply("Пидор, зайди в голосовой канал!")
        return
    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    guild_id = ctx.guild.id
    status_message = None  # Сообщение, которое будем редактировать

    # Создаем событие отмены и сохраняем его
    cancellation_event = threading.Event()
    download_cancellation_events[guild_id] = cancellation_event

    try:
        async with download_lock:
            await ctx.channel.typing()
            audio_file_to_play, title = None, "Без названия"

            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
                title = attachment.filename
                status_message = await ctx.reply(f"Обрабатываю файл: {title}...")
                audio_file_to_play = await process_file(ctx, attachment)
            elif query:
                if query.startswith("http"):
                    status_message = await ctx.reply("Скачиваю аудио по ссылке...")
                    audio_file_to_play, title = await asyncio.to_thread(download_audio, query, cancellation_event)
                else:
                    status_message = await ctx.reply(f"Ищу на YouTube: '{query}'...")
                    videos = await asyncio.to_thread(search_youtube, query)
                    if not videos:
                        await status_message.edit(content="По твоему запросу ничего не найдено.")
                        return
                    video_url = videos[0].get('webpage_url')
                    title = videos[0].get('title', 'Без названия')
                    await status_message.edit(content=f"Нашел: '{title}'. Скачиваю...")
                    audio_file_to_play, _ = await asyncio.to_thread(download_audio, video_url, cancellation_event)
            else:
                await ctx.reply("Пидор, прикрепи файл, дай ссылку или напиши, что искать!")
                return

            # Проверяем, был ли бот отключен во время выполнения
            if not ctx.voice_client or not ctx.voice_client.is_connected() or cancellation_event.is_set():
                print("Команда play отменена, так как бот был отключен.")
                if status_message:
                    await status_message.delete()
                # Удаляем файл, если он успел скачаться
                if audio_file_to_play and os.path.exists(audio_file_to_play):
                    os.remove(audio_file_to_play)
                return

            if audio_file_to_play:
                if guild_id not in queues:
                    queues[guild_id] = []
                song = {'file': audio_file_to_play, 'title': title}
                queues[guild_id].append(song)

                if not ctx.voice_client.is_playing():
                    if status_message:
                        await status_message.delete()  # Удаляем статусное сообщение
                    await play_next(ctx)
                else:
                    if status_message:
                        await status_message.edit(content=f"Добавлено в очередь: {title}")
            else:
                if status_message:
                    await status_message.edit(content="Не удалось обработать твой запрос и получить аудиофайл.")

    finally:
        # В любом случае удаляем событие отмены после завершения команды
        download_cancellation_events.pop(guild_id, None)


# --- КОНЕЦ ПЕРЕПИСАННОЙ КОМАНДЫ ---

@bot.command(name="queue", help="Показать текущую очередь песен.")
async def queue(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and queues[guild_id]:
        embed = discord.Embed(title="Очередь воспроизведения", color=discord.Color.blue())
        for i, song in enumerate(queues[guild_id]):
            embed.add_field(name=f"{i + 1}. {song['title']}", value="\u200b", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send("Очередь пуста.")


@bot.command(name="vikini", help="Пропустить текущую песню")
async def vikini(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        await ctx.reply("Пропускаю гамно...")
        ctx.voice_client.stop()
    else:
        await ctx.reply("Бот не воспроизводит гамно.")


@bot.command(name="viydi", help="Выкинуть бота с канала")
async def viydi(ctx):
    if ctx.voice_client and ctx.voice_client.is_connected():
        await ctx.reply("Пидор тупой!")
        await ctx.voice_client.disconnect()
    else:
        await ctx.reply("Пидор, я даже не в голосовом канале!")


async def text_to_speech(text: str) -> str:
    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as file:
            await communicate.save(file.name)
            return file.name
    except Exception as e:
        print(f"Ошибка при генерации речи с edge_tts: {e}")
        return None


@bot.command()
async def say(ctx, *, text: str = None):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.reply("Вы должны находиться в голосовом канале.")
        return
    if not text:
        await ctx.reply("Введите запрос!")
        return

    await ctx.typing()
    file_path = await text_to_speech(text)
    if not file_path:
        await ctx.reply("Не удалось сгенерировать речь.")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await asyncio.sleep(0.5)

    ctx.voice_client.play(discord.FFmpegPCMAudio(executable=ffmpeg, source=file_path),
                          after=lambda e: os.remove(file_path))


@bot.command(name="speak", help="Заставляет бота говорить в голосовом канале.")
async def speak(ctx, *, text: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.reply("Вы должны находиться в голосовом канале.")
        return

    channel = ctx.author.voice.channel
    await ctx.typing()
    bot_response_text = ""
    try:
        user_id = ctx.author.id
        if user_id not in memory:
            memory[user_id] = deque(maxlen=MEMORY_SIZE)
        memory[user_id].append({"role": "user", "parts": [text]})
        model = genai.GenerativeModel(MODEL)
        chat = model.start_chat(history=list(memory[user_id]))
        response = chat.send_message(text)
        bot_response_text = response.text
        memory[user_id].append({"role": "model", "parts": [bot_response_text]})
        save_memory()
    except Exception as e:
        await ctx.reply(f"Ошибка при генерации ответа: {e}")
        return

    file_path = await text_to_speech(bot_response_text)
    if not file_path:
        await ctx.reply("Не удалось сгенерировать речь.")
        return

    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await asyncio.sleep(0.5)

    ctx.voice_client.play(discord.FFmpegPCMAudio(executable=ffmpeg, source=file_path),
                          after=lambda e: os.remove(file_path))


bot.run(TOKEN)