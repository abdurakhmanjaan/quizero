# SIA Quiz Poll Bot — Admin File Upload

Bot Telegram'ning haqiqiy Quiz Poll formatida ishlaydi va admin fayl tashlab yangi fan qo'sha oladi.

## Ishga tushirish

```bash
cd ~/Downloads/sia_quiz_poll_admin_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## Admin orqali fan qo'shish

Telegramda botga kiring:

```text
/admin
```

Keyin:
1. `➕ Fayldan fan qo'shish`
2. Fan nomini yozing
3. `.txt`, `.docx`, `.pdf` fayl yuboring
4. Bot savollarni avtomatik ajratadi

## Qabul qilinadigan formatlar

### HEMIS ko'rinish:
```text
Savol matni?
====
to'g'ri javob
====
xato javob
====
xato javob
++++
```
Bu formatda odatda birinchi variant to'g'ri deb olinadi.

### A/B/C/D ko'rinish:
```text
Python nima?
A) Dasturlash tili #
B) O'yin
C) Virus
D) Brauzer
```
`#`, `*`, `[+]`, `(+)`, `✅` belgisi to'g'ri javobni bildiradi.

### Correct/Answer ko'rinish:
```text
Python nima?
A) Dasturlash tili
B) O'yin
C) Virus
D) Brauzer
Answer: A
```

## Admin buyruqlar

```text
/admin
/edit ID VARIANT
```

Masalan:
```text
/edit 29 3
```
