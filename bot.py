import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "expenses.db"


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def parse_expense(text: str) -> Optional[Tuple[str, int]]:
    match = re.match(r"^(.+?)\s+(\d[\d\s.,]*)$", text.strip())
    if not match:
        return None

    description = match.group(1).strip()
    amount_text = match.group(2).replace(" ", "").replace(",", ".")

    try:
        amount = round(float(amount_text))
    except ValueError:
        return None

    if not description or amount <= 0:
        return None

    return description, amount


def parse_expense_lines(text: str) -> Tuple[list[Tuple[str, int]], list[str]]:
    expenses = []
    errors = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        parsed = parse_expense(line)
        if parsed is None:
            errors.append(line)
            continue

        expenses.append(parsed)

    return expenses, errors


def format_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def add_expense(user_id: int, description: str, amount: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO expenses (user_id, description, amount, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, description, amount, datetime.now().isoformat(timespec="seconds")),
        )


def get_today_total(user_id: int) -> int:
    today = datetime.now().date().isoformat()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM expenses
            WHERE user_id = ? AND date(created_at) = ?
            """,
            (user_id, today),
        ).fetchone()

    return int(row[0])


def get_last_expenses(user_id: int, limit: int = 10) -> list[tuple[int, str, int, str]]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, description, amount, created_at
            FROM expenses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()


def delete_last_expense(user_id: int) -> Optional[Tuple[str, int]]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, description, amount
            FROM expenses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            return None

        expense_id, description, amount = row
        connection.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))

    return description, amount


def delete_today_expenses(user_id: int) -> int:
    today = datetime.now().date().isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM expenses
            WHERE user_id = ? AND date(created_at) = ?
            """,
            (user_id, today),
        )

    return cursor.rowcount


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Пиши расходы в формате: кофе 35000\n\n"
        "Команды:\n"
        "/today — сумма за сегодня\n"
        "/last — последние траты\n"
        "/undo — удалить последнюю трату\n"
        "/clear_today — очистить сегодняшние траты"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    total = get_today_total(update.effective_user.id)
    await update.message.reply_text(f"Сегодня: {format_amount(total)}")


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = get_last_expenses(update.effective_user.id)

    if not rows:
        await update.message.reply_text("Пока нет записанных трат.")
        return

    lines = []
    for _, description, amount, created_at in rows:
        time = datetime.fromisoformat(created_at).strftime("%d.%m %H:%M")
        lines.append(f"{time} — {description}: {format_amount(amount)}")

    await update.message.reply_text("\n".join(lines))


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deleted = delete_last_expense(update.effective_user.id)

    if deleted is None:
        await update.message.reply_text("Удалять пока нечего.")
        return

    description, amount = deleted
    total = get_today_total(update.effective_user.id)
    await update.message.reply_text(
        f"Удалила: {description} — {format_amount(amount)}\n"
        f"Сегодня теперь: {format_amount(total)}"
    )


async def clear_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = delete_today_expenses(update.effective_user.id)

    if count == 0:
        await update.message.reply_text("За сегодня записей нет.")
        return

    await update.message.reply_text(f"Очистила сегодняшние траты: {count}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    expenses, errors = parse_expense_lines(update.message.text)

    if not expenses:
        await update.message.reply_text(
            "Не поняла расход. Напиши так: кофе 35000"
        )
        return

    for description, amount in expenses:
        add_expense(update.effective_user.id, description, amount)

    total = get_today_total(update.effective_user.id)

    if len(expenses) == 1:
        description, amount = expenses[0]
        message = f"Записала: {description} — {format_amount(amount)}"
    else:
        added_total = sum(amount for _, amount in expenses)
        message = (
            f"Записала трат: {len(expenses)}\n"
            f"Сумма записи: {format_amount(added_total)}"
        )

    if errors:
        message += "\n\nНе поняла строки:\n" + "\n".join(errors)

    message += f"\nСегодня всего: {format_amount(total)}"
    await update.message.reply_text(message)


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError("Add BOT_TOKEN to .env before running the bot.")

    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        level=logging.INFO,
    )

    init_db()

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("last", last))
    application.add_handler(CommandHandler("undo", undo))
    application.add_handler(CommandHandler("clear_today", clear_today))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()


if __name__ == "__main__":
    main()
