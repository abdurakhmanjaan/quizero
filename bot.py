import asyncio
import json
import os
import random
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, PollAnswer
from dotenv import load_dotenv

try:
    from docx import Document
except Exception:
    Document = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
QUESTIONS_FILE = DATA_DIR / "questions.json"
STATS_FILE = DATA_DIR / "user_stats.json"

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("BOT_TOKEN .env faylida yozilmagan. BotFather tokenini kiriting.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SESSIONS: Dict[int, Dict[str, Any]] = {}
POLL_MAP: Dict[str, Dict[str, Any]] = {}
ADMIN_UPLOAD: Dict[int, Dict[str, Any]] = {}


def load_questions() -> List[Dict[str, Any]]:
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_questions(questions: List[Dict[str, Any]]) -> None:
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)


def load_stats() -> Dict[str, Any]:
    if not STATS_FILE.exists():
        return {}
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_stats(stats: Dict[str, Any]) -> None:
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(s: str) -> str:
    s = s.replace("\ufeff", "")
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def strip_correct_marks(s: str) -> Tuple[str, bool]:
    original = s
    marks = ["✅", "✔", "[+]", "(+)", "{+}", "#", "*"]
    is_correct = False
    for m in marks:
        if m in s:
            is_correct = True
            s = s.replace(m, "")
    s = re.sub(r"\s+", " ", s).strip(" ;,.\n\t")
    return s.strip(), is_correct


def read_file_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in [".txt", ".csv"]:
        for enc in ["utf-8", "utf-16", "cp1251", "latin-1"]:
            try:
                return path.read_text(encoding=enc)
            except Exception:
                pass
        return path.read_bytes().decode("utf-8", errors="ignore")

    if suffix == ".docx":
        if Document is None:
            raise RuntimeError("python-docx o'rnatilmagan.")
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        # Also read tables
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append("\n".join(cells))
        return "\n".join(parts)

    if suffix == ".pdf":
        if PdfReader is None:
            raise RuntimeError("pypdf o'rnatilmagan.")
        reader = PdfReader(str(path))
        texts = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                pass
        return "\n".join(texts)

    raise RuntimeError("Faqat .txt, .docx, .pdf fayllar qabul qilinadi.")


def parse_hemis_format(text: str) -> List[Dict[str, Any]]:
    """Format: question ==== option ==== option ++++ ; first option is usually correct."""
    items = []
    blocks = re.split(r"\n?\+{3,}\n?", text)
    for block in blocks:
        parts = [p.strip() for p in re.split(r"\n?={3,}\n?", block) if p.strip()]
        if len(parts) >= 3:
            question = clean_text(parts[0])
            options = []
            correct_index = 0
            for i, opt in enumerate(parts[1:]):
                opt, mark = strip_correct_marks(clean_text(opt))
                if mark:
                    correct_index = i
                if opt:
                    options.append(opt)
            if question and len(options) >= 2:
                items.append({"question": question, "options": options[:10], "answer_index": correct_index})
    return items


def parse_abcd_format(text: str) -> List[Dict[str, Any]]:
    """
    Supports:
    Question
    A) option
    B) option
    C) option #
    D) option
    Answer: C
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items = []
    q_lines = []
    opts = []
    correct_letter = None

    opt_re = re.compile(r"^([A-Ja-j])[\)\.\:\-]\s*(.+)$")
    ans_re = re.compile(r"^(?:answer|javob|to.?g.?ri javob|correct)\s*[:\-]\s*([A-Ja-j]|\d+)", re.I)

    def flush():
        nonlocal q_lines, opts, correct_letter
        if q_lines and len(opts) >= 2:
            q = clean_text(" ".join(q_lines))
            options = []
            correct_idx = None
            for idx, (letter, txt, marked) in enumerate(opts):
                txt, mark2 = strip_correct_marks(txt)
                marked = marked or mark2
                if marked:
                    correct_idx = idx
                options.append(txt)
            if correct_letter:
                for idx, (letter, _, _) in enumerate(opts):
                    if letter.upper() == correct_letter.upper():
                        correct_idx = idx
                        break
                if correct_idx is None and correct_letter.isdigit():
                    n = int(correct_letter)
                    if 1 <= n <= len(opts):
                        correct_idx = n - 1
            if correct_idx is None:
                correct_idx = 0
            if q and all(options):
                items.append({"question": q, "options": options[:10], "answer_index": min(correct_idx, 9)})
        q_lines = []
        opts = []
        correct_letter = None

    for ln in lines:
        ans = ans_re.match(ln)
        if ans:
            correct_letter = ans.group(1)
            continue

        m = opt_re.match(ln)
        if m:
            letter = m.group(1).upper()
            txt = m.group(2).strip()
            txt_clean, marked = strip_correct_marks(txt)
            opts.append((letter, txt_clean, marked))
            continue

        # New question starts after options if line is not answer/options
        if opts and q_lines:
            flush()
        q_lines.append(ln)

    flush()
    return items


def parse_numbered_format(text: str) -> List[Dict[str, Any]]:
    """
    Supports:
    1. Question?
       1) option
       2) option
       3) option #
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    normalized = []
    for ln in lines:
        ln = re.sub(r"^\d+[\.\)]\s+(?=.*\?)", "\nQUESTION: ", ln)
        normalized.append(ln)
    return parse_abcd_format("\n".join(normalized))


def parse_questions_from_text(text: str) -> List[Dict[str, Any]]:
    text = clean_text(text)
    all_items = []

    # 1) HEMIS separators
    if "====" in text or "++++" in text:
        all_items.extend(parse_hemis_format(text))

    # 2) A/B/C/D
    all_items.extend(parse_abcd_format(text))

    # Deduplicate by question
    seen = set()
    result = []
    for item in all_items:
        qkey = re.sub(r"\s+", " ", item["question"].lower()).strip()
        if qkey in seen:
            continue
        seen.add(qkey)
        if len(item["question"]) > 300:
            item["question"] = item["question"][:297] + "..."
        item["options"] = [opt[:100] for opt in item["options"] if opt.strip()]
        if len(item["options"]) >= 2:
            item["answer_index"] = max(0, min(int(item.get("answer_index", 0)), len(item["options"]) - 1))
            result.append(item)
    return result


def add_questions_to_db(subject: str, parsed: List[Dict[str, Any]]) -> Tuple[int, int]:
    questions = load_questions()
    max_id = max([int(q.get("id", 0)) for q in questions] or [0])
    existing = {re.sub(r"\s+", " ", q.get("question", "").lower()).strip() for q in questions}

    added = 0
    skipped = 0
    for item in parsed:
        qkey = re.sub(r"\s+", " ", item["question"].lower()).strip()
        if qkey in existing:
            skipped += 1
            continue
        max_id += 1
        questions.append({
            "id": max_id,
            "subject": subject,
            "question": item["question"],
            "options": item["options"],
            "answer_index": item["answer_index"]
        })
        existing.add(qkey)
        added += 1

    save_questions(questions)
    return added, skipped


def get_subjects() -> List[str]:
    return sorted({q.get("subject", "Sun'iy intellekt") for q in load_questions()})


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Fan tanlash", callback_data="menu:subjects")],
        [InlineKeyboardButton(text="📊 Natijalarim", callback_data="menu:stats")],
        [InlineKeyboardButton(text="❌ Xato savollar", callback_data="menu:wrongs")],
    ])


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Fayldan fan qo'shish", callback_data="admin:add_subject")],
        [InlineKeyboardButton(text="📚 Fanlar ro'yxati", callback_data="admin:subjects")],
        [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")],
    ])


def subjects_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📘 {sub}", callback_data=f"subject:{sub}")] for sub in get_subjects()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(subject: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 30 soniya", callback_data=f"time:{subject}:30"),
         InlineKeyboardButton(text="⏱ 1 daqiqa", callback_data=f"time:{subject}:60")],
        [InlineKeyboardButton(text="⬅️ Fanlarga qaytish", callback_data="menu:subjects")]
    ])


def blocks_menu(subject: str, seconds: int) -> InlineKeyboardMarkup:
    qs = [q for q in load_questions() if q.get("subject", "Sun'iy intellekt") == subject]
    rows = []
    for start in range(0, len(qs), 30):
        end = min(start + 30, len(qs))
        rows.append([InlineKeyboardButton(text=f"📝 {start+1}-{end} savollar", callback_data=f"start:{subject}:{seconds}:{start}:{end}")])
    rows.append([InlineKeyboardButton(text="🎲 Random 30 ta", callback_data=f"random:{subject}:{seconds}")])
    rows.append([InlineKeyboardButton(text="⬅️ Vaqt tanlash", callback_data=f"subject:{subject}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_result_menu(has_wrongs: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔁 Yana test ishlash", callback_data="menu:subjects")],
            [InlineKeyboardButton(text="📊 Natijalarim", callback_data="menu:stats")]]
    if has_wrongs:
        rows.insert(0, [InlineKeyboardButton(text="❌ Xato savollarni qayta ishlash", callback_data="menu:wrongs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def normalize_for_poll(q: Dict[str, Any]) -> Dict[str, Any]:
    options = list(q["options"])
    original_correct = int(q.get("answer_index", 0))
    pairs = [(opt, idx == original_correct) for idx, opt in enumerate(options)]
    random.shuffle(pairs)
    q2 = dict(q)
    q2["poll_options"] = [p[0] for p in pairs]
    q2["poll_correct_option_id"] = next(i for i, p in enumerate(pairs) if p[1])
    return q2


def update_user_stats(user_id: int, total: int, correct: int, wrong_ids: List[int]) -> None:
    stats = load_stats()
    uid = str(user_id)
    rec = stats.setdefault(uid, {"total_tests": 0, "total_questions": 0, "total_correct": 0, "wrong_ids": []})
    rec["total_tests"] += 1
    rec["total_questions"] += total
    rec["total_correct"] += correct
    saved = set(rec.get("wrong_ids", []))
    saved.update(wrong_ids)
    session = SESSIONS.get(user_id)
    if session:
        saved -= set(session.get("correct_ids", []))
    rec["wrong_ids"] = sorted(saved)
    save_stats(stats)


async def send_next_question(user_id: int) -> None:
    session = SESSIONS.get(user_id)
    if not session:
        return
    index = session["index"]
    questions = session["questions"]

    if index >= len(questions):
        total = len(questions)
        correct = session["correct"]
        wrong_ids = session["wrong_ids"]
        percent = round(correct / total * 100, 1) if total else 0
        update_user_stats(user_id, total, correct, wrong_ids)
        SESSIONS.pop(user_id, None)
        await bot.send_message(
            user_id,
            f"✅ Test tugadi!\n\n📚 Fan: {session['subject']}\n📌 Savollar: {total} ta\n✅ To'g'ri: {correct} ta\n❌ Xato: {len(wrong_ids)} ta\n📈 Foiz: {percent}%",
            reply_markup=after_result_menu(bool(wrong_ids))
        )
        return

    q = normalize_for_poll(questions[index])
    session["current_question"] = q

    title = f"{index+1}/{len(questions)}. {q['question']}"
    title = title[:300]
    opts = [str(x).strip()[:100] for x in q["poll_options"]]

    msg = await bot.send_poll(
        chat_id=user_id,
        question=title,
        options=opts,
        type="quiz",
        correct_option_id=q["poll_correct_option_id"],
        is_anonymous=False,
        open_period=session["seconds"],
        explanation=f"To'g'ri javob: {opts[q['poll_correct_option_id']]}"[:200],
    )

    POLL_MAP[msg.poll.id] = {"user_id": user_id, "question": q}
    asyncio.create_task(auto_next_after_timeout(user_id, msg.poll.id, session["seconds"] + 2))


async def auto_next_after_timeout(user_id: int, poll_id: str, delay: int) -> None:
    await asyncio.sleep(delay)
    session = SESSIONS.get(user_id)
    data = POLL_MAP.get(poll_id)
    if not session or not data:
        return
    current = session.get("current_question")
    if current and current.get("id") == data["question"].get("id"):
        qid = int(current["id"])
        if qid not in session["answered_ids"]:
            session["wrong_ids"].append(qid)
            session["answered_ids"].add(qid)
            session["index"] += 1
            await bot.send_message(user_id, "⏰ Vaqt tugadi. Keyingi savol.")
            await send_next_question(user_id)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Assalomu alaykum!\n\nBoshlash uchun fan tanlang.", reply_markup=main_menu())


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu menyu faqat admin uchun.")
        return
    await message.answer("Admin menyu:", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin:add_subject")
async def cb_admin_add(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Admin emas.", show_alert=True)
        return
    ADMIN_UPLOAD[call.from_user.id] = {"step": "subject"}
    await call.message.answer("Yangi fan nomini yozing. Masalan: Kiberxavfsizlik")
    await call.answer()


@dp.callback_query(F.data == "admin:subjects")
async def cb_admin_subjects(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Admin emas.", show_alert=True)
        return
    qs = load_questions()
    counts = {}
    for q in qs:
        counts[q.get("subject", "Noma'lum")] = counts.get(q.get("subject", "Noma'lum"), 0) + 1
    text = "📚 Fanlar:\n\n" + "\n".join([f"• {k}: {v} ta savol" for k, v in counts.items()])
    await call.message.answer(text or "Fanlar topilmadi.")
    await call.answer()


@dp.message(F.document)
async def upload_file(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    state = ADMIN_UPLOAD.get(message.from_user.id)
    if not state or state.get("step") != "file":
        await message.answer("Fayl qo'shish uchun avval /admin → Fayldan fan qo'shish ni bosing.")
        return

    doc = message.document
    filename = doc.file_name or "uploaded.txt"
    suffix = Path(filename).suffix.lower()
    if suffix not in [".txt", ".docx", ".pdf", ".csv"]:
        await message.answer("Faqat .txt, .docx, .pdf yoki .csv fayl yuboring.")
        return

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / filename
        await bot.download(doc, destination=path)
        try:
            text = read_file_text(path)
            parsed = parse_questions_from_text(text)
        except Exception as e:
            await message.answer(f"Faylni o'qishda xato: {e}")
            return

    if not parsed:
        await message.answer(
            "Savollar topilmadi.\n\n"
            "Eng yaxshi format:\n"
            "Savol?\n====\nTo'g'ri javob\n====\nXato javob\n====\nXato javob\n++++\n\n"
            "Yoki:\nSavol?\nA) javob #\nB) javob\nC) javob"
        )
        return

    subject = state["subject"]
    added, skipped = add_questions_to_db(subject, parsed)
    ADMIN_UPLOAD.pop(message.from_user.id, None)

    await message.answer(
        f"✅ Fan qo'shildi: {subject}\n\n"
        f"📄 Fayldan topildi: {len(parsed)} ta savol\n"
        f"➕ Bazaga qo'shildi: {added} ta\n"
        f"♻️ Takror savol o'tkazib yuborildi: {skipped} ta\n\n"
        "Endi /start orqali fanlar ro'yxatida chiqadi.",
        reply_markup=admin_menu()
    )


@dp.message(Command("edit"))
async def cmd_edit(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat admin uchun.")
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Format: /edit SAVOL_ID TOGRI_VARIANT_RAQAMI\nMasalan: /edit 29 3")
        return
    qid, variant_no = int(parts[1]), int(parts[2])
    qs = load_questions()
    for q in qs:
        if int(q.get("id", -1)) == qid:
            if not 1 <= variant_no <= len(q["options"]):
                await message.answer(f"Variant 1 dan {len(q['options'])} gacha bo'lishi kerak.")
                return
            q["answer_index"] = variant_no - 1
            save_questions(qs)
            await message.answer(f"✅ ID {qid}: to'g'ri javob {variant_no}-variant qilindi.")
            return
    await message.answer("Bunday ID topilmadi.")


@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    SESSIONS.pop(message.from_user.id, None)
    ADMIN_UPLOAD.pop(message.from_user.id, None)
    await message.answer("Jarayon to'xtatildi.", reply_markup=main_menu())


@dp.message()
async def text_handler(message: Message):
    if message.from_user.id in ADMIN_IDS:
        state = ADMIN_UPLOAD.get(message.from_user.id)
        if state and state.get("step") == "subject":
            subject = message.text.strip()
            if len(subject) < 2:
                await message.answer("Fan nomi juda qisqa. Qayta yozing.")
                return
            state["subject"] = subject
            state["step"] = "file"
            await message.answer(
                f"Fan nomi: {subject}\n\nEndi test faylini yuboring: .txt, .docx yoki .pdf"
            )
            return


@dp.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery):
    await call.message.edit_text("Asosiy menyu:", reply_markup=main_menu())
    await call.answer()


@dp.callback_query(F.data == "menu:subjects")
async def cb_subjects(call: CallbackQuery):
    await call.message.edit_text("Fan nomini tanlang:", reply_markup=subjects_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("subject:"))
async def cb_subject(call: CallbackQuery):
    subject = call.data.split(":", 1)[1]
    await call.message.edit_text(f"📘 Fan: {subject}\n\nVaqtni tanlang:", reply_markup=settings_menu(subject))
    await call.answer()


@dp.callback_query(F.data.startswith("time:"))
async def cb_time(call: CallbackQuery):
    _, subject, seconds = call.data.split(":", 2)
    await call.message.edit_text(f"📘 Fan: {subject}\n⏱ Vaqt: {seconds} soniya\n\nTest bo'limini tanlang:", reply_markup=blocks_menu(subject, int(seconds)))
    await call.answer()


@dp.callback_query(F.data.startswith("start:"))
async def cb_start_block(call: CallbackQuery):
    _, subject, seconds, start, end = call.data.split(":", 4)
    seconds, start, end = int(seconds), int(start), int(end)
    qs = [q for q in load_questions() if q.get("subject", "Sun'iy intellekt") == subject]
    selected = qs[start:end]
    if not selected:
        await call.answer("Savollar topilmadi.", show_alert=True)
        return
    SESSIONS[call.from_user.id] = {"subject": subject, "seconds": seconds, "questions": selected, "index": 0, "correct": 0, "wrong_ids": [], "correct_ids": [], "answered_ids": set(), "current_question": None}
    await call.message.answer(f"🚀 Test boshlandi!\n📘 Fan: {subject}\n📌 Savollar: {start+1}-{end}\n⏱ Har savol: {seconds} soniya")
    await call.answer()
    await send_next_question(call.from_user.id)


@dp.callback_query(F.data.startswith("random:"))
async def cb_random(call: CallbackQuery):
    _, subject, seconds = call.data.split(":", 2)
    seconds = int(seconds)
    qs = [q for q in load_questions() if q.get("subject", "Sun'iy intellekt") == subject]
    selected = random.sample(qs, min(30, len(qs)))
    SESSIONS[call.from_user.id] = {"subject": subject, "seconds": seconds, "questions": selected, "index": 0, "correct": 0, "wrong_ids": [], "correct_ids": [], "answered_ids": set(), "current_question": None}
    await call.message.answer(f"🎲 Random test boshlandi!\n📘 Fan: {subject}\n📌 Savollar: {len(selected)} ta\n⏱ Har savol: {seconds} soniya")
    await call.answer()
    await send_next_question(call.from_user.id)


@dp.callback_query(F.data == "menu:stats")
async def cb_stats(call: CallbackQuery):
    rec = load_stats().get(str(call.from_user.id), {})
    tq = rec.get("total_questions", 0)
    tc = rec.get("total_correct", 0)
    percent = round(tc / tq * 100, 1) if tq else 0
    await call.message.answer(
        f"📊 Natijalaringiz:\n\n📝 Testlar soni: {rec.get('total_tests', 0)}\n📌 Jami savollar: {tq}\n✅ To'g'ri javoblar: {tc}\n❌ Saqlangan xato savollar: {len(rec.get('wrong_ids', []))}\n📈 Umumiy foiz: {percent}%",
        reply_markup=main_menu()
    )
    await call.answer()


@dp.callback_query(F.data == "menu:wrongs")
async def cb_wrongs(call: CallbackQuery):
    wrong_ids = load_stats().get(str(call.from_user.id), {}).get("wrong_ids", [])
    if not wrong_ids:
        await call.message.answer("Sizda hozircha xato savollar yo'q.", reply_markup=main_menu())
        await call.answer()
        return
    qs = [q for q in load_questions() if int(q.get("id", -1)) in set(wrong_ids)]
    SESSIONS[call.from_user.id] = {"subject": "Xato savollar", "seconds": 30, "questions": qs, "index": 0, "correct": 0, "wrong_ids": [], "correct_ids": [], "answered_ids": set(), "current_question": None}
    await call.message.answer(f"❌ Xato savollar testi boshlandi. Jami: {len(qs)} ta")
    await call.answer()
    await send_next_question(call.from_user.id)


@dp.poll_answer()
async def poll_answer_handler(answer: PollAnswer):
    data = POLL_MAP.get(answer.poll_id)
    if not data:
        return
    user_id = answer.user.id
    if data["user_id"] != user_id:
        return
    session = SESSIONS.get(user_id)
    if not session:
        return
    q = data["question"]
    qid = int(q["id"])
    if qid in session["answered_ids"]:
        return
    selected = answer.option_ids[0] if answer.option_ids else -1
    if selected == int(q["poll_correct_option_id"]):
        session["correct"] += 1
        session["correct_ids"].append(qid)
    else:
        session["wrong_ids"].append(qid)
    session["answered_ids"].add(qid)
    session["index"] += 1
    await asyncio.sleep(1)
    await send_next_question(user_id)


async def main():
    print("Bot started with admin file upload parser...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
