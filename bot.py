import os
from dotenv import load_dotenv
import re
import sqlite3
import datetime as dt
from typing import Optional

import discord
from discord import app_commands

load_dotenv()
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
# print(CHANNEL_ID)

# Connect to SQLite database and a table with the following columns
# No name is counted twice and multiple rows are allowed
db = sqlite3.connect("lineups.db")
db.execute("""
CREATE TABLE IF NOT EXISTS appearances (
    message_id INTEGER,
    user_id    INTEGER,
    role       TEXT,
    show_date  TEXT,
    PRIMARY KEY (message_id, user_id, role)
)""")
db.commit()

# Discord client to allow discord bot to function
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
# Command tree (/something ...)
tree = app_commands.CommandTree(client)

# LOOKUP TABLES AND PATTERNS
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

DATE_RE = re.compile(
    r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ]{0,15}?)\s*:")

# PARSING LOGIC
def parse_lineup(text: str, posted_at: dt.datetime):
    """Return (show_date, [(user_id, role), ...]) or None if not a lineup."""
    lines = text.splitlines()
    if not lines:
        return None

    # 1. The first line must start with a date like "Oct 28"
    m = DATE_RE.match(lines[0])
    if not m:
        return None
    month, day = MONTHS[m.group(1).lower()], int(m.group(2))

    # 2. Figure out the year it was sent
    posted = posted_at.date()
    try:
        show_date = dt.date(posted.year, month, day)
        if (show_date - posted).days < -180:   # e.g. posted in Dec for a Jan show
            show_date = dt.date(posted.year + 1, month, day)
    except ValueError:                          # e.g. "Feb 30"
        return None

    # 3. Every other line: find mentions and role label
    entries = []
    for line in lines[1:]:
        ids = MENTION_RE.findall(line)
        if not ids:
            continue
        r = ROLE_RE.match(line)
        role = r.group(1).strip().upper() if r else "OTHER"
        for uid in ids:
            entries.append((int(uid), role))

    return show_date, entries

# CONNECT PARSER TO DB
def record_lineup(message: discord.Message) -> bool:
    # Always clear this message's old rows first
    db.execute("DELETE FROM appearances WHERE message_id = ?", (message.id,))

    # if not a lineup SKIP
    parsed = parse_lineup(message.content, message.created_at)
    if parsed is None:
        db.commit()
        return False

    # if a line up LOAD IT 
    show_date, entries = parsed
    for user_id, role in entries:
        db.execute(
            "INSERT OR IGNORE INTO appearances VALUES (?, ?, ?, ?)",
            (message.id, user_id, role, show_date.isoformat()),
        )
    db.commit()
    return True

# EVENT DETECTION (WHEN SOMETHING HAPPENS IN DISCORD)
@client.event
async def on_ready():
    # uplaods /commands in menu
    await tree.sync()   
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message: discord.Message):
    # make sure in right channel and ignore messages from other bots (so it doesn't react to
    # own or other bot mesgs))
    if message.channel.id == CHANNEL_ID and not message.author.bot:
        # runs the parse, delete, and insert pipeline
        record_lineup(message)

@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    # get the after edits of a lineup, correct what was edited
    if after.channel.id == CHANNEL_ID:
        record_lineup(after)

@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # handles deleted msgs
    if payload.channel_id == CHANNEL_ID:
        db.execute("DELETE FROM appearances WHERE message_id = ?", (payload.message_id,))
        db.commit()

def year_filter(year: Optional[int]):
    y = str(year) if year else None
    return y, y   # used as ? IS NULL OR substr(show_date,1,4) = ?

# make sure no one gets pinged from the command
NO_PINGS = discord.AllowedMentions.none()

# SLASH COMMANDS
# admin command to rewrite data
@tree.command(name="backfill", description="Import all past lineups from the channel")
@app_commands.checks.has_permissions(manage_guild=True)
async def backfill(interaction: discord.Interaction):
    # allows for a 15min buffer rather than 3 seconds (since loading up a lot of data takes a while)
    await interaction.response.defer(ephemeral=True)
    channel = client.get_channel(CHANNEL_ID)
    found = 0
    async for msg in channel.history(limit=None, oldest_first=True):
        if not msg.author.bot and record_lineup(msg):
            found += 1
    await interaction.followup.send(f"Imported {found} lineups.")

# counts lineup msgs a person appears in (distinct - no repeats)
@tree.command(name="shows", description="How many shows has someone done?")
async def shows(interaction: discord.Interaction, member: discord.Member,
                year: Optional[int] = None):
    count = db.execute(
        """SELECT COUNT(DISTINCT message_id) FROM appearances
           WHERE user_id = ? AND (? IS NULL OR substr(show_date,1,4) = ?)""",
        (member.id, *year_filter(year)),
    ).fetchone()[0]
    label = f" in {year}" if year else ""
    await interaction.response.send_message(
        f"{member.mention}: **{count}** shows{label}", allowed_mentions=NO_PINGS, ephemeral=True)

# sort by role/lables (FUTURE NOTE NEED TOO MAKE SURE LION IS ASSOCIATED WITH L1, L2, L3...)
@tree.command(name="roles", description="Breakdown of roles someone has played")
async def roles(interaction: discord.Interaction, member: discord.Member,
                year: Optional[int] = None):
    rows = db.execute(
        """SELECT role, COUNT(DISTINCT message_id) FROM appearances
           WHERE user_id = ? AND (? IS NULL OR substr(show_date,1,4) = ?)
           GROUP BY role ORDER BY 2 DESC""",
        (member.id, *year_filter(year)),
    ).fetchall()
    text = "\n".join(f"{r}: {c}" for r, c in rows) or "No shows found."
    await interaction.response.send_message(
        f"{member.mention}\n{text}", allowed_mentions=NO_PINGS, ephemeral=True)

# filtered log for people
@tree.command(name="mylog", description="Recent shows for a member")
async def mylog(interaction: discord.Interaction, member: discord.Member):
    rows = db.execute(
        """SELECT show_date, GROUP_CONCAT(role, '/') FROM appearances
           WHERE user_id = ? GROUP BY message_id
           ORDER BY show_date DESC LIMIT 15""",
        (member.id,),
    ).fetchall()
    text = "\n".join(f"{d}: {r}" for d, r in rows) or "No shows found."
    await interaction.response.send_message(
        f"{member.mention}\n{text}", allowed_mentions=NO_PINGS, ephemeral=True)

# show leaderboard of EVERYONE
@tree.command(name="leaderboard", description="Show counts for everyone")
async def leaderboard(interaction: discord.Interaction, year: Optional[int] = None):
    rows = db.execute(
        """SELECT user_id, COUNT(DISTINCT message_id) c FROM appearances
           WHERE (? IS NULL OR substr(show_date,1,4) = ?)
           GROUP BY user_id ORDER BY c DESC""",
        year_filter(year),
    ).fetchall()
    if not rows:
        await interaction.response.send_message("No data yet.")
        return
    text = "\n".join(f"{i}. <@{uid}>: {c}" for i, (uid, c) in enumerate(rows, 1))
    await interaction.response.send_message(text[:1990], allowed_mentions=NO_PINGS, ephemeral=True)


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])