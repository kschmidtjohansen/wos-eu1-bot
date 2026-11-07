"""Everything players read from the bot, in every language.

English and Polish are built in. On the dashboard (Translations page) admins can change any text and add
more languages; their versions are stored in the database and win over the built-in ones. Anything that is
missing in a language falls back to English, so a half-done translation never breaks the bot.
Placeholders such as {id} are filled in by the bot and must stay in the translated text.
"""
import json
import re
from typing import Optional

LANGUAGE_NAMES = {
    "en": "English", "pl": "Polski", "da": "Dansk", "de": "Deutsch", "fr": "Français", "es": "Español",
    "it": "Italiano", "nl": "Nederlands", "sv": "Svenska", "no": "Norsk", "fi": "Suomi", "pt": "Português",
    "cs": "Čeština", "hu": "Magyar", "ro": "Română", "uk": "Українська", "ru": "Русский", "tr": "Türkçe",
    "lt": "Lietuvių", "lv": "Latviešu", "et": "Eesti", "el": "Ελληνικά", "bg": "Български", "hr": "Hrvatski",
}

# (key, group shown on the dashboard, English text)
_EN = [
    # opening a ticket
    ("open.blocked", "Opening a ticket", "you can't send reports or suggestions right now{until}. Please contact a moderator if you think this is a mistake."),
    ("open.until", "Opening a ticket", " until {time}"),
    ("open.bugs_off", "Opening a ticket", "bug reports are currently turned off."),
    ("open.sugs_off", "Opening a ticket", "suggestions are currently turned off."),
    ("open.not_setup", "Opening a ticket", "this isn't fully set up yet – please let an admin know."),
    ("open.exists", "Opening a ticket", "you already have an open ticket: {ticket}"),
    ("open.cooldown", "Opening a ticket", "please wait {minutes} more minute(s) before sending another report."),
    ("open.ready", "Opening a ticket", "your ticket is ready: {ticket}"),
    ("open.language", "Opening a ticket", "🌍 Language"),
    ("open.language_set", "Opening a ticket", "✅ I'll talk to you in English from now on."),
    # bug questions
    ("bug.title", "Bug ticket", "🐛 Bug report"),
    ("bug.title_edit", "Bug ticket", "✏️ Edit your bug report"),
    ("bug.body", "Bug ticket", "**Step 1** – pick the **area** in the menu, then press **Add details** (short title, chief name, player UID, game version).\n"
                               "**Step 2** – answer these questions **in one message**:\n\n"
                               "**1. What is the bug?**\n**2. Do you know why it is happening?**\n"
                               "**3. What is the correct way the feature should work?**\n"
                               "**4. Attach a picture or video** to the same message{again}\n\n"
                               "When you press **Send**, I'll show you a preview where you can **Submit**, **Edit** or **Delete** your report."),
    ("bug.again", "Bug ticket", " (attachments have to be added again)"),
    ("bug.template_name", "Bug ticket", "Template you can copy"),
    ("bug.template", "Bug ticket", "What is the bug?\n\nDo you know why it is happening?\n\nWhat is the correct way the feature should work?"),
    ("bug.area_ph", "Bug ticket", "Where is the problem? (area)"),
    ("bug.area_set", "Bug ticket", "✅ Area: **{area}**"),
    ("bug.area_gone", "Bug ticket", "That area doesn't exist any more."),
    ("bug.details_btn", "Bug ticket", "Add details"),
    ("modal.title", "Bug ticket", "Bug details"),
    ("modal.short", "Bug ticket", "Short title (what is wrong, in a few words)"),
    ("modal.short_ph", "Bug ticket", "e.g. Horse sinks when mounted near water"),
    ("modal.chief", "Bug ticket", "Chief name (optional)"),
    ("modal.uid", "Bug ticket", "Player UID (optional)"),
    ("modal.version", "Bug ticket", "Game version"),
    ("modal.uid_bad", "Bug ticket", "⚠️ The player UID must be a number (4–20 digits). Press **Add details** to try again."),
    ("modal.saved", "Bug ticket", "✅ Details saved. Now write your answers in one message."),
    ("modal.similar", "Bug ticket", "🔎 **This may already be reported:**"),
    ("modal.similar_fixed", "Bug ticket", " (fixed in {version})"),
    ("modal.similar_tail", "Bug ticket", "If it is the same problem you don't need to report it again – you can delete this ticket. If it is different, just carry on."),
    # suggestion questions
    ("sug.title", "Suggestion ticket", "💡 Create a suggestion"),
    ("sug.title_edit", "Suggestion ticket", "✏️ Edit your suggestion"),
    ("sug.body", "Suggestion ticket", "Write your suggestion **in one message** (max {max} characters). You can add a picture too.\n\n"
                                      "When you send it, I'll show you a preview where you can **Submit**, **Edit** or **Delete** it."),
    # preview
    ("pv.title", "Preview", "📋 Preview – is this ready to send?"),
    ("pv.no_text", "Preview", "*(no text)*"),
    ("pv.f_title", "Preview", "Title"),
    ("pv.f_title_missing", "Preview", "⚠️ *(missing – press Add details)*"),
    ("pv.f_area", "Preview", "Area"),
    ("pv.f_area_missing", "Preview", "⚠️ *(missing – use the menu)*"),
    ("pv.f_version", "Preview", "Game version"),
    ("pv.f_chief", "Preview", "Chief / UID"),
    ("pv.f_files", "Preview", "Attachments"),
    ("pv.footer", "Preview", "Submit = send to the moderators · Edit = change it · Delete = discard"),
    ("pv.submit", "Preview", "Submit"),
    ("pv.edit", "Preview", "Edit"),
    ("pv.delete", "Preview", "Delete"),
    ("pv.previous", "Preview", "Your previous message (copy & edit)"),
    # buttons that don't work for this person / moment
    ("guard.closed", "Ticket messages", "This ticket is closed."),
    ("guard.owner", "Ticket messages", "Only the person who opened this ticket can use this."),
    ("guard.outdated", "Ticket messages", "This preview is outdated – please use the latest one."),
    # submitting
    ("sub.need", "Ticket messages", "Please add {missing} first."),
    ("sub.need_area", "Ticket messages", "an **area** (menu)"),
    ("sub.need_title", "Ticket messages", "a **short title** (Add details)"),
    ("sub.and", "Ticket messages", " and "),
    ("sub.blocked", "Ticket messages", "You can't send reports or suggestions right now. Please contact a moderator."),
    ("sub.lost", "Ticket messages", "I can't find your report message any more (was it deleted?). Please send it again."),
    ("sub.sug_text", "Ticket messages", "Please write your suggestion as text (a picture alone isn't enough)."),
    ("sub.sug_long", "Ticket messages", "Your suggestion is {length} characters – the limit is {max}. Please send a shorter version."),
    ("sub.sug_fail", "Ticket messages", "⚠️ I couldn't deliver your suggestion to the moderators. Please tell an admin, then press **Submit** again."),
    ("sub.sug_done_title", "Ticket messages", "✅ Submitted as suggestion #{id}"),
    ("sub.sug_done", "Ticket messages", "✅ Your suggestion was sent to the moderators as **#{id}**. This ticket closes in a few seconds."),
    ("sub.bug_fail", "Ticket messages", "⚠️ I couldn't deliver your report to the moderators. Please tell an admin (the mod channel may be missing or I lack access), then press **Submit** again."),
    ("sub.bug_done_title", "Ticket messages", "✅ Submitted as bug #{id}"),
    ("sub.bug_done", "Ticket messages", "✅ Your report was sent to the moderators as **bug #{id}**. I'll DM you when a moderator handles it (keep your DMs open).\nThis ticket closes in a few seconds."),
    ("sub.discarded", "Ticket messages", "🗑️ Draft discarded. Closing this ticket…"),
    # info ticket (moderator needs more information)
    ("info.ticket", "Info ticket", "🎫 **Ticket for bug #{id}** – {mod} needs more information.\n{player}, please answer the moderator's questions here.\nOriginal report: {link}"),
    # direct messages
    ("dm.passed", "Direct messages", "✅ Your bug report **#{id}** was accepted and passed on to the admins. Thank you!"),
    ("dm.rejected", "Direct messages", "❌ Your bug report **#{id}** was closed – it's a duplicate of an existing report or the feature is working as intended. Thanks for taking the time to report it!"),
    ("dm.investigating", "Direct messages", "🔍 A moderator needs more information about your bug report **#{id}**. Please reply in your ticket: {link}"),
    ("dm.being_investigated", "Direct messages", "🔍 Your bug report **#{id}** is now being investigated by the admins."),
    ("dm.fixed", "Direct messages", "✔️ Good news! Your bug report **#{id}** has been fixed. Thank you for reporting it!"),
    ("dm.admin_rejected", "Direct messages", "❌ After a closer look, the admins closed your bug report **#{id}**. Thanks for reporting it!"),
    ("dm.duplicate", "Direct messages", "❌ Your bug report **#{id}** is a duplicate of **#{original}** ({status}). We already have it on our list – thanks for reporting!"),
    ("dm.follow_fixed", "Direct messages", "✔️ Good news! The problem from your report **#{id}** (same as **#{original}**) has been fixed. Thank you for reporting it!"),
    ("dm.points", "Direct messages", "\n🏆 You have earned **{points} point(s)** for this report – see `/points`."),
    ("dm.sug_accepted", "Direct messages", "💡 Your suggestion **#{id}** was accepted – thank you!"),
    ("dm.sug_rejected", "Direct messages", "💡 Your suggestion **#{id}** was reviewed but won't be taken forward this time. Thanks for sharing your idea!"),
    ("dm.reply_head", "Direct messages", "💬 **Message from the moderators about your bug report #{id}:**"),
    # /mybugs
    ("my.title", "Player commands", "📋 Your reports"),
    ("my.none", "Player commands", "You haven't sent any bug reports or suggestions yet."),
    ("my.bugs", "Player commands", "Bug reports"),
    ("my.sugs", "Player commands", "Suggestions"),
    ("st.new", "Player commands", "⏳ Waiting for a moderator"),
    ("st.investigating", "Player commands", "🔍 A moderator needs more info – check your ticket"),
    ("st.passed", "Player commands", "📨 With the admins"),
    ("st.being_investigated", "Player commands", "🔍 Being investigated"),
    ("st.fixed", "Player commands", "✔️ Fixed"),
    ("st.rejected", "Player commands", "❌ Closed"),
    ("st.admin_rejected", "Player commands", "❌ Closed"),
    ("st.sug_new", "Player commands", "⏳ Waiting for a decision"),
    ("st.sug_accepted", "Player commands", "✅ Accepted"),
    ("st.sug_rejected", "Player commands", "❌ Not taken forward"),
    # gift codes
    ("gift.new", "Gift codes", "🎁 New gift code!"),
    ("gift.expired", "Gift codes", "⌛ Gift code expired"),
    ("gift.reward", "Gift codes", "Reward"),
    ("gift.valid", "Gift codes", "Valid until"),
    ("gift.expired_at", "Gift codes", "Expired"),
    ("gift.never", "Gift codes", "Never expires"),
    ("gift.footer", "Gift codes", "/codes shows all active codes"),
    ("gift.list", "Gift codes", "🎁 Active gift codes"),
    ("gift.none", "Gift codes", "There are no active gift codes right now."),
    ("gift.until", "Gift codes", "until {time} ({relative})"),
    ("gift.off", "Gift codes", "Gift codes are not switched on."),
    # /language
    ("lang.set", "Player commands", "✅ Language set to **{name}**."),
    ("lang.auto", "Player commands", "✅ I'll use your Discord language again (**{name}**)."),
]

_PL = {
    "open.blocked": "nie możesz teraz wysyłać zgłoszeń ani sugestii{until}. Jeśli uważasz, że to pomyłka, skontaktuj się z moderatorem.",
    "open.until": " do {time}",
    "open.bugs_off": "zgłaszanie błędów jest obecnie wyłączone.",
    "open.sugs_off": "sugestie są obecnie wyłączone.",
    "open.not_setup": "to nie jest jeszcze w pełni skonfigurowane – daj znać administratorowi.",
    "open.exists": "masz już otwarty ticket: {ticket}",
    "open.cooldown": "poczekaj jeszcze {minutes} min, zanim wyślesz kolejne zgłoszenie.",
    "open.ready": "twój ticket jest gotowy: {ticket}",
    "open.language": "🌍 Język",
    "open.language_set": "✅ Od teraz będę pisać do Ciebie po polsku.",
    "bug.title": "🐛 Zgłoszenie błędu",
    "bug.title_edit": "✏️ Popraw swoje zgłoszenie",
    "bug.body": "**Krok 1** – wybierz **obszar** w menu, a potem naciśnij **Dodaj szczegóły** (krótki tytuł, nazwa wodza, UID gracza, wersja gry).\n"
                "**Krok 2** – odpowiedz na te pytania **w jednej wiadomości**:\n\n"
                "**1. Na czym polega błąd?**\n**2. Czy wiesz, dlaczego się pojawia?**\n"
                "**3. Jak ta funkcja powinna działać prawidłowo?**\n"
                "**4. Dołącz zdjęcie lub film** do tej samej wiadomości{again}\n\n"
                "Gdy naciśniesz **Wyślij**, pokażę podgląd, w którym możesz **Wysłać**, **Poprawić** albo **Usunąć** zgłoszenie.",
    "bug.again": " (załączniki trzeba dodać ponownie)",
    "bug.template_name": "Szablon do skopiowania",
    "bug.template": "Na czym polega błąd?\n\nCzy wiesz, dlaczego się pojawia?\n\nJak ta funkcja powinna działać prawidłowo?",
    "bug.area_ph": "Gdzie jest problem? (obszar)",
    "bug.area_set": "✅ Obszar: **{area}**",
    "bug.area_gone": "Ten obszar już nie istnieje.",
    "bug.details_btn": "Dodaj szczegóły",
    "modal.title": "Szczegóły błędu",
    "modal.short": "Krótki tytuł (co jest nie tak, w kilku słowach)",
    "modal.short_ph": "np. Koń tonie przy wsiadaniu nad wodą",
    "modal.chief": "Nazwa wodza (opcjonalnie)",
    "modal.uid": "UID gracza (opcjonalnie)",
    "modal.version": "Wersja gry",
    "modal.uid_bad": "⚠️ UID gracza musi być liczbą (4–20 cyfr). Naciśnij **Dodaj szczegóły**, aby spróbować ponownie.",
    "modal.saved": "✅ Szczegóły zapisane. Teraz napisz odpowiedzi w jednej wiadomości.",
    "modal.similar": "🔎 **Ten problem mógł już zostać zgłoszony:**",
    "modal.similar_fixed": " (naprawiono w {version})",
    "modal.similar_tail": "Jeśli to ten sam problem, nie musisz go zgłaszać ponownie – możesz usunąć ten ticket. Jeśli jest inny, po prostu kontynuuj.",
    "sug.title": "💡 Nowa sugestia",
    "sug.title_edit": "✏️ Popraw swoją sugestię",
    "sug.body": "Napisz swoją sugestię **w jednej wiadomości** (maks. {max} znaków). Możesz też dodać zdjęcie.\n\n"
                "Gdy ją wyślesz, pokażę podgląd, w którym możesz ją **Wysłać**, **Poprawić** albo **Usunąć**.",
    "pv.title": "📋 Podgląd – czy można wysłać?",
    "pv.no_text": "*(brak tekstu)*",
    "pv.f_title": "Tytuł",
    "pv.f_title_missing": "⚠️ *(brak – naciśnij Dodaj szczegóły)*",
    "pv.f_area": "Obszar",
    "pv.f_area_missing": "⚠️ *(brak – użyj menu)*",
    "pv.f_version": "Wersja gry",
    "pv.f_chief": "Wódz / UID",
    "pv.f_files": "Załączniki",
    "pv.footer": "Wyślij = do moderatorów · Popraw = zmień · Usuń = odrzuć",
    "pv.submit": "Wyślij",
    "pv.edit": "Popraw",
    "pv.delete": "Usuń",
    "pv.previous": "Twoja poprzednia wiadomość (skopiuj i popraw)",
    "guard.closed": "Ten ticket jest zamknięty.",
    "guard.owner": "Tylko osoba, która otworzyła ten ticket, może tego użyć.",
    "guard.outdated": "Ten podgląd jest nieaktualny – użyj najnowszego.",
    "sub.need": "Najpierw dodaj {missing}.",
    "sub.need_area": "**obszar** (menu)",
    "sub.need_title": "**krótki tytuł** (Dodaj szczegóły)",
    "sub.and": " i ",
    "sub.blocked": "Nie możesz teraz wysyłać zgłoszeń ani sugestii. Skontaktuj się z moderatorem.",
    "sub.lost": "Nie mogę już znaleźć Twojej wiadomości (czy została usunięta?). Wyślij ją ponownie.",
    "sub.sug_text": "Napisz sugestię tekstem (samo zdjęcie nie wystarczy).",
    "sub.sug_long": "Twoja sugestia ma {length} znaków – limit to {max}. Wyślij krótszą wersję.",
    "sub.sug_fail": "⚠️ Nie udało się dostarczyć sugestii moderatorom. Powiadom administratora, a potem naciśnij ponownie **Wyślij**.",
    "sub.sug_done_title": "✅ Wysłano jako sugestię #{id}",
    "sub.sug_done": "✅ Twoja sugestia trafiła do moderatorów jako **#{id}**. Ten ticket zamknie się za kilka sekund.",
    "sub.bug_fail": "⚠️ Nie udało się dostarczyć zgłoszenia moderatorom. Powiadom administratora, a potem naciśnij ponownie **Wyślij**.",
    "sub.bug_done_title": "✅ Wysłano jako błąd #{id}",
    "sub.bug_done": "✅ Twoje zgłoszenie trafiło do moderatorów jako **błąd #{id}**. Napiszę do Ciebie, gdy moderator się nim zajmie (miej otwarte wiadomości prywatne).\nTen ticket zamknie się za kilka sekund.",
    "sub.discarded": "🗑️ Szkic odrzucony. Zamykam ten ticket…",
    "info.ticket": "🎫 **Ticket dla błędu #{id}** – {mod} potrzebuje więcej informacji.\n{player}, odpowiedz tutaj na pytania moderatora.\nOryginalne zgłoszenie: {link}",
    "dm.passed": "✅ Twoje zgłoszenie **#{id}** zostało przyjęte i przekazane administratorom. Dziękujemy!",
    "dm.rejected": "❌ Twoje zgłoszenie **#{id}** zostało zamknięte – to duplikat istniejącego zgłoszenia albo funkcja działa zgodnie z założeniami. Dziękujemy za zgłoszenie!",
    "dm.investigating": "🔍 Moderator potrzebuje więcej informacji o Twoim zgłoszeniu **#{id}**. Odpowiedz w swoim tickecie: {link}",
    "dm.being_investigated": "🔍 Administratorzy badają teraz Twoje zgłoszenie **#{id}**.",
    "dm.fixed": "✔️ Dobra wiadomość! Błąd z Twojego zgłoszenia **#{id}** został naprawiony. Dziękujemy za zgłoszenie!",
    "dm.admin_rejected": "❌ Po dokładniejszym sprawdzeniu administratorzy zamknęli Twoje zgłoszenie **#{id}**. Dziękujemy za zgłoszenie!",
    "dm.duplicate": "❌ Twoje zgłoszenie **#{id}** to duplikat **#{original}** ({status}). Mamy je już na liście – dzięki za zgłoszenie!",
    "dm.follow_fixed": "✔️ Dobra wiadomość! Problem z Twojego zgłoszenia **#{id}** (taki sam jak **#{original}**) został naprawiony. Dziękujemy!",
    "dm.points": "\n🏆 Za to zgłoszenie zdobywasz **{points} pkt** – zobacz `/points`.",
    "dm.sug_accepted": "💡 Twoja sugestia **#{id}** została przyjęta – dziękujemy!",
    "dm.sug_rejected": "💡 Twoja sugestia **#{id}** została rozpatrzona, ale tym razem nie zostanie wdrożona. Dzięki za pomysł!",
    "dm.reply_head": "💬 **Wiadomość od moderatorów w sprawie Twojego zgłoszenia #{id}:**",
    "my.title": "📋 Twoje zgłoszenia",
    "my.none": "Nie wysłałeś(-aś) jeszcze żadnych zgłoszeń ani sugestii.",
    "my.bugs": "Zgłoszenia błędów",
    "my.sugs": "Sugestie",
    "st.new": "⏳ Czeka na moderatora",
    "st.investigating": "🔍 Moderator potrzebuje informacji – sprawdź ticket",
    "st.passed": "📨 U administratorów",
    "st.being_investigated": "🔍 W trakcie badania",
    "st.fixed": "✔️ Naprawiony",
    "st.rejected": "❌ Zamknięty",
    "st.admin_rejected": "❌ Zamknięty",
    "st.sug_new": "⏳ Czeka na decyzję",
    "st.sug_accepted": "✅ Przyjęta",
    "st.sug_rejected": "❌ Niewdrożona",
    "gift.new": "🎁 Nowy kod prezentowy!",
    "gift.expired": "⌛ Kod wygasł",
    "gift.reward": "Nagroda",
    "gift.valid": "Ważny do",
    "gift.expired_at": "Wygasł",
    "gift.never": "Nie wygasa",
    "gift.footer": "/codes pokazuje wszystkie aktywne kody",
    "gift.list": "🎁 Aktywne kody prezentowe",
    "gift.none": "Obecnie nie ma aktywnych kodów prezentowych.",
    "gift.until": "do {time} ({relative})",
    "gift.off": "Kody prezentowe nie są włączone.",
    "lang.set": "✅ Ustawiono język: **{name}**.",
    "lang.auto": "✅ Znów używam języka Twojego Discorda (**{name}**).",
}

DEFAULTS = {"en": {k: v for k, _, v in _EN}, "pl": _PL}
GROUPS = {k: g for k, g, _ in _EN}
KEYS = [k for k, _, _ in _EN]
_PH = re.compile(r"{(\w+)}")


def placeholders(text: str) -> set:
    return set(_PH.findall(text or ""))


class _Safe(dict):
    def __missing__(self, key):          # a wrong placeholder in a translation never crashes the bot
        return "{" + key + "}"


def fill(text: str, **kw) -> str:
    try:
        return text.format_map(_Safe(kw))
    except (ValueError, IndexError):
        return text


def lang_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code.upper())


def check_translation(key: str, text: str) -> Optional[str]:
    """Returns a problem description, or None if the text can be used."""
    if key not in GROUPS:
        return f"Unknown text '{key}'."
    if len(text) > 1800:
        return "Too long (max 1800 characters)."
    need, have = placeholders(DEFAULTS["en"][key]), placeholders(text)
    if have - need:
        return "Unknown placeholder " + ", ".join("{" + x + "}" for x in sorted(have - need)) + "."
    if need - have:
        return "Missing placeholder " + ", ".join("{" + x + "}" for x in sorted(need - have)) + " – keep it in the text."
    return None


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)
