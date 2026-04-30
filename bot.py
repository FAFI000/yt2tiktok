import os
import asyncio
import subprocess
import json
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import whisper

# ============================================
# CONFIG
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8438645885:AAFifKMRojljcLR8buxygwl3ZJgAbXPVlX0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7410774964")
MAX_PART_DURATION = 90
MIN_PART_DURATION = 45

DOWNLOADS_DIR = Path("downloads")
OUTPUT_DIR = Path("output")
DOWNLOADS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ============================================
# DOWNLOAD
# ============================================
def download_video(url: str) -> tuple:
    print(f"📥 كنحمل: {url}")
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info['id']
        title = info.get('title', 'فيديو')
        video_path = str(DOWNLOADS_DIR / f"{video_id}.mp4")
        return video_path, title

# ============================================
# SMART CUTS
# ============================================
def find_smart_cuts(video_path: str) -> list:
    print("🎙️ كنحلل الصوت...")
    audio_path = video_path.replace('.mp4', '.wav')
    subprocess.run([
        'ffmpeg', '-i', video_path,
        '-ar', '16000', '-ac', '1', '-y', audio_path
    ], check=True, capture_output=True)

    model = whisper.load_model("tiny")
    result = model.transcribe(audio_path, word_timestamps=True)

    probe = subprocess.run([
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', video_path
    ], capture_output=True, text=True)
    duration = float(json.loads(probe.stdout)['format']['duration'])
    print(f"⏱️ المدة: {duration/60:.1f} دقيقة")

    silences = []
    segments = result['segments']
    for i in range(len(segments) - 1):
        current_end = segments[i]['end']
        next_start = segments[i + 1]['start']
        gap = next_start - current_end
        if gap > 0.3:
            silences.append({'time': current_end + gap / 2, 'gap': gap})

    cuts = [0]
    current_pos = 0
    while current_pos < duration:
        target_end = current_pos + MAX_PART_DURATION
        min_end = current_pos + MIN_PART_DURATION
        if target_end >= duration:
            cuts.append(duration)
            break
        best_cut = None
        best_distance = float('inf')
        for silence in silences:
            t = silence['time']
            if min_end <= t <= target_end + 15:
                distance = abs(t - target_end)
                if distance < best_distance:
                    best_distance = distance
                    best_cut = t
        if best_cut:
            cuts.append(best_cut)
            current_pos = best_cut
        else:
            cuts.append(target_end)
            current_pos = target_end

    os.remove(audio_path)
    return cuts

# ============================================
# CUT VIDEO
# ============================================
def cut_video(video_path: str, cuts: list) -> list:
    print(f"✂️ كنقطع لـ {len(cuts)-1} partie...")
    video_name = Path(video_path).stem
    parts = []
    for i in range(len(cuts) - 1):
        start = cuts[i]
        end = cuts[i + 1]
        duration = end - start
        part_num = i + 1
        output_path = str(OUTPUT_DIR / f"{video_name}_part{part_num:03d}.mp4")
        print(f"  ✂️ Partie {part_num}: {start/60:.1f}→{end/60:.1f}min")
        subprocess.run([
            'ffmpeg', '-ss', str(start), '-i', video_path,
            '-t', str(duration), '-c:v', 'libx264', '-c:a', 'aac',
            '-preset', 'fast', '-crf', '23', '-y', output_path
        ], check=True, capture_output=True)
        parts.append(output_path)
    print(f"✅ تقطعو {len(parts)} parties!")
    return parts

# ============================================
# SEND TO TELEGRAM
# ============================================
async def send_to_telegram(parts: list, title: str):
    print("📤 كنسيفت لتيلغرام...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    total = len(parts)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"🎬 *{title}*\n✅ تقطع لـ *{total} parties*",
        parse_mode='Markdown'
    )
    for i, part_path in enumerate(parts, 1):
        print(f"  📨 كنسيفت {i}/{total}...")
        with open(part_path, 'rb') as f:
            await bot.send_video(
                chat_id=TELEGRAM_CHAT_ID,
                video=f,
                caption=f"🎬 *{title}*\n📌 Partie {i}/{total}",
                parse_mode='Markdown',
                supports_streaming=True
            )
        await asyncio.sleep(2)
    print("🎉 كولشي وصل!")

# ============================================
# BOT HANDLERS
# ============================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *YT2TikTok Bot*\n\nحط ليا لينك يوتيوب!\n\nمثال:\n`https://youtube.com/watch?v=...`",
        parse_mode='Markdown'
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if 'youtube.com' not in url and 'youtu.be' not in url:
        await update.message.reply_text("❌ حط لينك يوتيوب صحيح!")
        return

    msg = await update.message.reply_text("⏳ بدينا...")
    try:
        await msg.edit_text("📥 كنحمل الفيديو من يوتيوب...")
        video_path, title = download_video(url)

        await msg.edit_text("🎙️ كنحلل الصوت بـ Whisper...")
        cuts = find_smart_cuts(video_path)

        await msg.edit_text(f"✂️ كنقطع لـ {len(cuts)-1} parties...")
        parts = cut_video(video_path, cuts)

        await msg.edit_text(f"📤 كنسيفت {len(parts)} parties...")
        await send_to_telegram(parts, title)

        await msg.edit_text(
            f"🎉 *خلصنا!*\n✅ {len(parts)} parties وصلوك\n🎬 جاهزين للتيك توك!",
            parse_mode='Markdown'
        )

        os.remove(video_path)
        for part in parts:
            os.remove(part)

    except Exception as e:
        await msg.edit_text(f"❌ وقع خطأ:\n`{str(e)}`", parse_mode='Markdown')
        print(f"Error: {e}")

# ============================================
# MAIN
# ============================================
def main():
    print("🤖 Bot كيبدا...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    print("✅ Bot خدام!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
