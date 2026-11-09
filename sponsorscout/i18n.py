"""Internationalization (i18n) for SponsorScout.

Provides English (default) and Italian translations.
Usage:  from sponsorscout.i18n import _, set_locale, get_locale
        text = _("Search")
"""
from __future__ import annotations

import json

from sponsorscout.paths import USER_DATA_DIR

# ── current locale (runtime, persisted separately) ──────────────────────────

_locale: str = "en"
_qt_translator = None
_qt_locale = None


def _supported_locale(locale) -> str:
    return locale if isinstance(locale, str) and locale in ("en", "it") else "en"


def sync_qt_locale() -> None:
    """Use the app language for Qt-owned buttons/menus/file dialogs too.

    Called on the GUI thread when loading/changing locale, never by _() or
    worker messages. Pure scanner/CLI use does not require PySide6.
    """
    global _qt_translator, _qt_locale
    try:
        from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator, Qt
    except ImportError:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    # OS-native file pickers can follow the OS language instead of the app.
    # Qt-rendered pickers use the same translated catalog as message boxes.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    if _qt_locale == _locale:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator.deleteLater()
        _qt_translator = None
    _qt_locale = _locale
    if _locale == "it":
        translator = QTranslator(app)
        if translator.load("qtbase_it", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(translator)
            _qt_translator = translator
        else:
            # Retry if a catalog becomes available later (e.g. startup setup).
            _qt_locale = None
            import logging
            logging.getLogger(__name__).warning("Qt Italian translation catalog was not found")

# Config file for persisting language preference
_I18N_CONFIG = USER_DATA_DIR / "locale.json"


def set_locale(locale: str) -> None:
    """Set the active locale at runtime."""
    global _locale
    _locale = _supported_locale(locale)
    sync_qt_locale()
    # Persist to disk
    try:
        _I18N_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        _I18N_CONFIG.write_text(json.dumps({"locale": _locale}))
    except Exception:
        pass


def get_locale() -> str:
    """Return the current locale code ('en' or 'it')."""
    return _locale


def load_saved_locale() -> str:
    """Load persisted locale from disk. Returns 'en' if not found."""
    global _locale
    _locale = "en"
    try:
        if _I18N_CONFIG.exists():
            data = json.loads(_I18N_CONFIG.read_text())
            _locale = _supported_locale(data.get("locale", "en"))
    except Exception:
        _locale = "en"
    sync_qt_locale()
    return _locale


def get_available_locales() -> list[str]:
    """Return list of available locale codes."""
    return ["en", "it"]


def get_locale_name(locale: str) -> str:
    """Return human-readable locale name."""
    names = {"en": "English", "it": "Italiano"}
    return names.get(locale, locale)


# ── Translation dictionary ─────────────────────────────────────────────────
# Each key is the English string. English is the identity (key == value).
# Italian provides precise translations.

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {
        'Action needed: access denied on page %d; no CAPTCHA was confirmed. Retry in Chromium or stop.': 'Action needed: access denied on page %d; no CAPTCHA was confirmed. Retry in Chromium or stop.',
        "Finalizing scan results...": "Finalizing scan results...",
        'Action needed: bot verification appeared on page %d — please solve the check in the Chromium window to continue...': 'Action needed: bot verification appeared on page %d — please solve the check in the Chromium window to continue...',
        'Action needed: please solve the human-verification check in the Chromium window...': 'Action needed: please solve the human-verification check in the Chromium window...',
        'Harvesting page %d — %d jobs ingested (%d new, %d refreshed)...': 'Harvesting page %d — %d jobs ingested (%d new, %d refreshed)...',
        # ── Header ─────────────────────────────────────────────────────
        "Verified sponsorship-focused jobs from official career pages and ATS boards":
            "Verified sponsorship-focused jobs from official career pages and ATS boards",
        "Ready": "Ready",
        "Scan complete.": "Scan complete.",

        # ── Tab names ──────────────────────────────────────────────────
        "Dashboard": "Dashboard",
        "Applications": "Applications",
        "ATS Health": "ATS Health",
        "AI Tailor": "AI Tailor",
        "Tools": "Tools",

        # ── Search tab ─────────────────────────────────────────────────
        "Title:": "Title:",
        "Company:": "Company:",
        "Country:": "Country:",
        "Search": "Search",
        "Clear": "Clear",
        "Sponsorship:": "Sponsorship:",
        "Remote:": "Remote:",
        "Experience:": "Experience:",
        "Experience": "Experience",
        "No experience requirement found in the job description": "No experience requirement found in the job description",
        "The ad mentions experience without stating a figure or a level.": "The ad mentions experience without stating a figure or a level.",
        "Seniority level \"{level}\" — the ad states no number of years.": "Seniority level \"{level}\" — the ad states no number of years.",
        # Verdict tooltips (Sponsor / Blue Card / Reloc): these quote the sentence
        # that produced the Y / N / ? so a verdict can be audited without opening
        # the posting. "Unknown" is spelled out because a bare "?" next to a "Y"
        # reads like a rendering bug rather than a missing statement.
        "Confidence: {c:.0%}": "Confidence: {c:.0%}",
        "Found in the job description:": "Found in the job description:",
        "{col}: {v}\n\nThe job description makes no explicit statement about this. Unknown means the ad did not say — it is not a \"no\".":
            "{col}: {v}\n\nThe job description makes no explicit statement about this. Unknown means the ad did not say — it is not a \"no\".",
        "{col}: {v}\n\nNo supporting sentence was recorded for this verdict.":
            "{col}: {v}\n\nNo supporting sentence was recorded for this verdict.",
        "Sort:": "Sort:",
        "Objective:": "Objective:",
        "Balanced": "Balanced",
        "Strict quality": "Strict quality",
        "Visa sponsor": "Visa sponsor",
        "Local EU": "Local EU",
        "Blue Card focus": "Blue Card focus",
        "Relocation": "Relocation",
        "All": "All",
        "Regex": "Regex",
        "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).":
            "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).",
        "Invalid regular expression": "Invalid regular expression",
        "Regex disabled — invalid pattern:\n{error}":
            "Regex disabled — invalid pattern:\n{error}",
        "Any (incl. unknown)": "Any (incl. unknown)",
        "Unknown / Not classified": "Unknown / Not classified",
        "Intern": "Intern",
        "Entry": "Entry",
        "Mid": "Mid",
        "Senior": "Senior",
        "Lead": "Lead",
        "Exec": "Exec",
        "Best match": "Best match",
        "Latest": "Latest",
        "Sponsored Only": "Sponsored Only",
        "Remote EU": "Remote EU",
        "Remote EMEA": "Remote EMEA",
        "Remote Global": "Remote Global",
        "Remote Only": "Remote Only",
        "Hybrid": "Hybrid",
        "jobs found": "jobs found",
        "job found": "job found",

        # ── AI Rating panel ────────────────────────────────────────────
        "AI Job Rating & Eligibility": "AI Job Rating & Eligibility",
        "Rate this job": "Rate this job",
        "Tailor CV & Letter": "Tailor CV & Letter",
        "Select a job, then click 'Rate this job'.":
            "Select a job, then click 'Rate this job'.",
        "No job selected.": "No job selected.",
        "Rating against your saved CV profile":
            "Rating against your saved CV profile",
        "No CV on file — paste yours in AI Tailor tab for personalised results":
            "No CV on file — paste yours in AI Tailor tab for personalised results",
        "Rating will use your saved CV profile":
            "Rating will use your saved CV profile",
        "Contacting Gemini API...": "Contacting Gemini API...",
        "Contacting custom AI endpoint...": "Contacting custom AI endpoint...",
        "Contacting {provider} OpenAI-compatible API...":
            "Contacting {provider} OpenAI-compatible API...",

        # ── Search: right-click context menu ───────────────────────────
        "Open in browser": "Open in browser",
        "Save to Applications": "Save to Applications",
        "Rate with AI": "Rate with AI",
        "Tailor CV & Cover Letter": "Tailor CV & Cover Letter",

        # ── Dashboard ──────────────────────────────────────────────────
        "Companies": "Companies",
        "Verified Jobs": "Verified Jobs",
        "Sponsored": "Sponsored",
        "Remote": "Remote",
        "EU Blue Card": "EU Blue Card",
        "New this week": "New this week",
        "Top companies by sponsorship score":
            "Top companies by sponsorship score",
        "Jobs by country": "Jobs by country",

        # ── Applications tab ───────────────────────────────────────────
        "Saved applications": "Saved applications",
        "Refresh": "Refresh",
        "Remove Selected": "Remove Selected",
        "Edit selected": "Edit selected",
        "Notes:": "Notes:",
        "saved": "saved",
        "applied": "applied",
        "interview": "interview",
        "offer": "offer",
        "rejected": "rejected",

        # ── ATS Health tab ─────────────────────────────────────────────
        "ATS Connector Health": "ATS Connector Health",
        "Success/failure rates per connector after each scan.":
            "Success/failure rates per connector after each scan.",

        # ── Tools tab ──────────────────────────────────────────────────
        "Scanner": "Scanner",
        "Scan all 111 companies via their official ATS APIs.":
            "Scan all 111 companies via their official ATS APIs.",
        "Status:": "Status:",
        "idle": "idle",
        "Scan Now": "Scan Now",
        "Auto (1 h)": "Auto (1 h)",
        "Stop": "Stop",
        "Pause": "Pause",
        "Showing {a}–{b} of {n} results":
            "Showing {a}–{b} of {n} results",
        "Previous page": "Previous page",
        "Next page": "Next page",
        "Rows per page": "Rows per page",
        "Scan Log:": "Scan Log:",
        "Data Quality": "Data Quality",
        "Remove duplicate jobs and companies from the database.":
            "Remove duplicate jobs and companies from the database.",
        "Run Dedup": "Run Dedup",
        "Clear Scan Data": "Clear Scan Data",
        "The database already contains no scanned data.":
            "The database already contains no scanned data.",
        "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?":
            "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?",
        "Scan data cleared": "Scan data cleared",
        "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.":
            "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.",
        "Stale data cleared": "Stale data cleared",
        "Removed {n} expired job(s) from the database.":
            "Removed {n} expired job(s) from the database.",
        "No expired jobs to remove — the database is already clean.":
            "No expired jobs to remove — the database is already clean.",
        "View Per-Company Log": "View Per-Company Log",
        "Download Scan Log": "Download Scan Log",
        "Select a scan run first.": "Select a scan run first.",
        "Scan log downloaded": "Scan log downloaded",
        "Scan log saved to:\n{path}": "Scan log saved to:\n{path}",
        "CSV files (*.csv);;All files (*.*)": "CSV files (*.csv);;All files (*.*)",
        "Could not save scan log:\n{error}": "Could not save scan log:\n{error}",
        "The file appears empty:\n{path}": "The file appears empty:\n{path}",
        "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}":
            "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}",
        "AI Settings": "AI Settings",
        "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.":
            "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.",
        "AI API Key / Token:": "AI API Key / Token:",
        "Save Key": "Save Key",
        "Provider:": "Provider:",
        "Model:": "Model:",
        "(type any model name; chips are provider-specific)":
            "(type any model name; chips are provider-specific)",
        "Base URL:": "Base URL:",
        "(auto-filled for built-in providers; required for custom)":
            "(auto-filled for built-in providers; required for custom)",
        "type the exact model ID from your provider":
            "type the exact model ID from your provider",
        "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):":
            "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):",
        "Save Prompt": "Save Prompt",
        "Reset to Default": "Reset to Default",
        "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):":
            "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):",
        "Save CV Prompt": "Save CV Prompt",
        "Reset": "Reset",
        "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):":
            "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):",
        "Save Letter Prompt": "Save Letter Prompt",
        "Company Discovery": "Company Discovery",
        "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.":
            "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.",
        "Query:": "Query:",
        "Discover": "Discover",
        "Freshness Check": "Freshness Check",
        "Verify jobs still exist online — auto-expires dead links.":
            "Verify jobs still exist online — auto-expires dead links.",
        "Max jobs:": "Max jobs:",
        "Run": "Run",

        # ── AI Tailor tab ──────────────────────────────────────────────
        "AI CV & Cover Letter Tailor": "AI CV & Cover Letter Tailor",
        "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below":
            "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below",
        "How to use": "How to use",
        "No job selected — use Search tab or paste a JD below":
            "No job selected — use Search tab or paste a JD below",
        "Job URL:": "Job URL:",
        "Fetch JD": "Fetch JD",
        "— or paste full JD below —": "— or paste full JD below —",
        "Use this JD": "Use this JD",
        "My CV (stored locally)": "My CV (stored locally)",
        "Paste your current CV once — it's saved for all future tailoring sessions.":
            "Paste your current CV once — it's saved for all future tailoring sessions.",
        "Save CV": "Save CV",
        "CV saved": "CV saved",
        "Base Cover Letter (template for AI)":
            "Base Cover Letter (template for AI)",
        "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.":
            "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.",
        "Save Template": "Save Template",
        "Template saved": "Template saved",
        "Generate": "Generate",
        "Tailor My CV": "Tailor My CV",
        "Write Cover Letter": "Write Cover Letter",
        "Both": "Both",
        "Result": "Result",
        "CV": "CV",
        "Cover Letter": "Cover Letter",
        "Copy": "Copy",
        "Fetching…": "Fetching…",

        # ── Messages: tailor tab ───────────────────────────────────────
        "Empty JD": "Empty JD",
        "Paste a job description first.": "Paste a job description first.",
        "No URL": "No URL",
        "Enter a job post URL first.": "Enter a job post URL first.",
        "Empty CV": "Empty CV",
        "Paste your CV first.": "Paste your CV first.",
        "No Job Description": "No Job Description",
        "Fetch or paste a job description first, then click 'Use this JD'.":
            "Fetch or paste a job description first, then click 'Use this JD'.",
        "No CV": "No CV",
        "Paste your CV in the 'My CV' box and save it first.":
            "Paste your CV in the 'My CV' box and save it first.",
        "Copied to clipboard!": "Copied to clipboard!",
        "Select a row": "Select a row",
        "Click a row first, then click Remove.":
            "Click a row first, then click Remove.",
        "Remove": "Remove",
        "Search error": "Search error",
        "Error": "Error",
        "Missing": "Missing",
        "Enter a search query.": "Enter a search query.",

        # ── Messages: AI settings ──────────────────────────────────────
        "Saved": "Saved",
        "AI API key saved.": "AI API key saved.",
        "Empty": "Empty",
        "Prompt cannot be empty.": "Prompt cannot be empty.",
        "Custom prompt saved.": "Custom prompt saved.",
        "CV tailoring prompt saved.": "CV tailoring prompt saved.",
        "Cover letter prompt saved.": "Cover letter prompt saved.",
        "Reset prompt to default?": "Reset prompt to default?",
        "Reset CV tailoring prompt to default?":
            "Reset CV tailoring prompt to default?",
        "Reset cover letter prompt to default?":
            "Reset cover letter prompt to default?",
        "Unknown provider": "Unknown provider",
        "Missing model": "Missing model",
        "Type a model name first.": "Type a model name first.",
        "Missing base URL": "Missing base URL",

        # ── Messages: first run ────────────────────────────────────────
        "Welcome to SponsorScout": "Welcome to SponsorScout",

        # ── Messages: general ──────────────────────────────────────────
        "No job selected": "No job selected",
        "Select a job in the Search tab first.":
            "Select a job in the Search tab first.",
        "Dashboard data could not be loaded.":
            "Dashboard data could not be loaded.",
        "Dedup complete": "Dedup complete",
        "Probing ATS boards for": "Probing ATS boards for",
        "Found": "Found",
        "candidate(s).": "candidate(s).",
        "No new companies found.":
            "No new companies found.",
        "Try: 'analyst', 'backend', 'data engineer'":
            "Try: 'analyst', 'backend', 'data engineer'",
        "Discovery done.": "Discovery done.",
        "Discovery failed.": "Discovery failed.",
        "Running discovery…": "Running discovery…",
        "running…": "running…",
        "Scanning…": "Scanning…",
        "auto — every 1 h": "auto — every 1 h",
        "stopped": "stopped",
        "Failed.": "Failed.",
        "Verified": "Verified",

        # ── Tooltip: How to Use ────────────────────────────────────────
        "AI Tailor — How to Use": "AI Tailor — How to Use",

        # ── Tooltip: JD section ────────────────────────────────────────
        "Job Description": "Job Description",

        # ── Tooltip: CV section ────────────────────────────────────────
        "My CV": "My CV",

        # ── Tooltip: Cover Letter section ──────────────────────────────
        "Base Cover Letter Template": "Base Cover Letter Template",

        # ── Tooltip: Generate section ──────────────────────────────────
        "Generate Buttons": "Generate Buttons",

        # ── Tooltip: Result section ────────────────────────────────────
        "Result Area": "Result Area",

        # ── Language toggle ────────────────────────────────────────────
        # ── Dashboard ──────────────────────────────────────────────────
        "Total Companies": "Total Companies",
        "Sponsored Jobs": "Sponsored Jobs",
        "Remote Jobs": "Remote Jobs",
        "Top Companies by Sponsorship": "Top Companies by Sponsorship",
        "Jobs by Country": "Jobs by Country",
        "Company": "Company",
        "Country": "Country",
        "Jobs": "Jobs",
        "Top Sponsor": "Top Sponsor",
        "Rescan Companies": "Rescan Companies",
        "Unknown": "Unknown",

        # ── Tools descriptions ────────────────────────────────────────
        "Scanner description": "Start a job scan across all seeded companies — every ATS board and career page is crawled, then each job is enriched from its detail page. Live output appears below.",
        "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.":
            "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.",
        "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.":
            "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.",
        "Continue the paused scan where it stopped — same run, no loss.":
            "Continue the paused scan where it stopped — same run, no loss.",
        "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.":
            "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.",
        "Looking up the last stopped scan…":
            "Looking up the last stopped scan…",
        "Paused": "Paused",
        "Pausing…": "Pausing…",
        "Stopping…": "Stopping…",
        "Stopped.": "Stopped.",
        "Stop the running scan? Everything found so far is kept and can be resumed later.":
            "Stop the running scan? Everything found so far is kept and can be resumed later.",
        "Scan History description": "Every past scan run. Select a row to view or download its per-company log with errors.",
        "Resume": "Resume",
        "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.":
            "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.",
        "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.":
            "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.",
        "Resuming scan": "Resuming scan",
        "Nothing to resume — no stopped scan with unfinished companies.":
            "Nothing to resume — no stopped scan with unfinished companies.",
        "Could not find a scan to resume:\n{error}":
            "Could not find a scan to resume:\n{error}",
        "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.":
            "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.",
        "(+{n} companies added to seeds since the stop — they are included.)":
            "(+{n} companies added to seeds since the stop — they are included.)",
        "Resuming — {done}/{total} done.":
            "Resuming — {done}/{total} done.",
        "Resume {run} — {done}/{total} done, {remaining} remaining.":
            "Resume {run} — {done}/{total} done, {remaining} remaining.",
        "resumed": "resumed",
        "resumed from": "resumed from",
        "Starting scan…": "Starting scan…",
        "ATS": "ATS",
        "Career": "Career",
        "Scan": "Scan",
        "Finished.": "Finished.",
        "Cancelled — partial progress shown.": "Cancelled — partial progress shown.",
        "Data Quality description": "Review trusted posting aliases with a backup and audit before merging; explicitly clear expired jobs or scanned data.",
        "Freshness Check description": "Re-verify saved jobs against their live pages and mark expired listings.",
        "{n} row(s) promoted into jobs.": "{n} row(s) promoted into jobs.",
        "{n} already in jobs (updated).": "{n} already in jobs (updated).",
        "{n} skipped (missing URL/title or error).": "{n} skipped (missing URL/title or error).",
        "Close this dialog to refresh the Search list.": "Close this dialog to refresh the Search list.",
        "Errors:": "Errors:",
        "…and {n} more.": "…and {n} more.",
        "No value under cursor": "No value under cursor",
        "Copy {header}": "Copy {header}",
        "Copy row": "Copy row",
        "Open URL": "Open URL",
        "Cell": "Cell",
    },

    # ═══════════════════════════════════════════════════════════════════
    # ITALIAN TRANSLATIONS
    # ═══════════════════════════════════════════════════════════════════
    "it": {
        'Action needed: access denied on page %d; no CAPTCHA was confirmed. Retry in Chromium or stop.': 'Azione richiesta: accesso negato alla pagina %d; nessun CAPTCHA confermato. Riprova in Chromium o interrompi.',
        "Finalizing scan results...": "Finalizzazione dei risultati della scansione...",
        'Action needed: bot verification appeared on page %d — please solve the check in the Chromium window to continue...': 'Azione richiesta: verifica anti-bot alla pagina %d — completa il controllo nella finestra Chromium per continuare...',
        'Action needed: please solve the human-verification check in the Chromium window...': 'Azione richiesta: completa la verifica umana nella finestra Chromium...',
        'Harvesting page %d — %d jobs ingested (%d new, %d refreshed)...': 'Raccolta pagina %d — %d offerte acquisite (%d nuove, %d aggiornate)...',
        # ── F-26 (2026-10-22): entries recovered from the docstring splice ──
        "Launch Assisted Browser and Ingest": "Avvia browser assistito e acquisisci",
        "Load and Ingest": "Carica e acquisisci",
        "Error: %s": "Errore: %s",
        "Pages read: %d. Pagination stopped because: %s": "Pagine lette: %d. Paginazione interrotta perché: %s",
        "Categorised: %d / %d (%s%%) | With Skills: %d (%s%%)": "Classificati: %d / %d (%s%%) | Con competenze: %d (%s%%)",
        "Successfully re-analysed %d jobs with updated granular categories and skills!": "Rianalizzati con successo %d lavori con categorie e competenze dettagliate!",
        "Re-Analyse Database Skills": "Rianalizza competenze nel database",
        "Refresh Insights": "Aggiorna Approfondimenti",
        "Refreshing Insights...": "Aggiornamento Approfondimenti...",
        "Extract skills, domains, and categories from saved job descriptions in SQLite and refresh charts": "Estrai competenze, settori e categorie dalle descrizioni salvate in SQLite e aggiorna i grafici",
        "Conservative CPU/RAM protection is active on <=4-core / <=8 GB hardware so your PC stays responsive.": "La protezione conservativa CPU/RAM è attiva su hardware <=4 core / <=8 GB per mantenere il PC reattivo.",
        "Leverage additional CPU cores and RAM on capable PCs (>4 cores, >8 GB RAM) to run more parallel background browsers and scan threads while keeping desktop headroom lag-free.": "Sfrutta core CPU e RAM aggiuntivi sui PC più potenti (>4 core, >8 GB RAM) per eseguire più browser e thread paralleli senza rallentare il sistema.",
        "%d of the harvested jobs were already in the database - they were refreshed, not duplicated.": "%d delle offerte raccolte erano già presenti nel database: sono state aggiornate, non duplicate.",
        "AI Prompt Kit": "Kit Prompt IA",
        "Action": "Azione",
        "Categorised: %d / %d (%.1f%%) | With Skills: %d (%.1f%%)": "Categorizzate: %d / %d (%.1f%%) | Con competenze: %d (%.1f%%)",
        "Category": "Categoria",
        "Clear Filter (Show All)": "Rimuovi filtro (Mostra tutto)",
        "Click any slice or legend item to filter the dashboard by this category": "Fai clic su uno spicchio o una voce della legenda per filtrare la bacheca per questa categoria",
        "Click any slice or legend item to filter the dashboard by this industry sector": "Fai clic su uno spicchio o una voce della legenda per filtrare la bacheca per questo settore industriale",
        "Click center to clear filter": "Fai clic al centro per rimuovere il filtro",
        "Click to filter dashboard": "Fai clic per filtrare la bacheca",
        "Click to unfilter": "Fai clic per rimuovere il filtro",
        "Employer Domain": "Settore del datore di lavoro",
        "Employer Industry Domain (Hiring Company Sector)": "Settore industriale del datore di lavoro (Settore azienda)",
        "Failed: ": "Errore: ",
        "Filtered": "Filtrate",
        "Filtered by %s: %s": "Filtrato per %s: %s",
        "Harvested 0 jobs. Verification may not have been completed.": "0 offerte raccolte. La verifica potrebbe non essere stata completata.",
        "High-Performance Mode (Multi-Threaded Parallel Browsers)": "Modalità Alte Prestazioni (Browser paralleli multi-thread)",
        "In-Demand Soft & Professional Skills": "Competenze trasversali e professionali più richieste",
        "In-Demand Technical Skills & Tools": "Competenze tecniche e strumenti più richiesti",
        "Ingestion Error": "Errore di acquisizione",
        "Ingestion Incomplete": "Acquisizione incompleta",
        "Ingestion completed, but 0 jobs were harvested. Please ensure the human verification was completed and job listings were visible before the browser window closed.": "Acquisizione completata, ma sono state raccolte 0 offerte. Assicurati che la verifica umana sia stata completata e che gli annunci fossero visibili prima della chiusura del browser.",
        "Ingestion completed: %d jobs were harvested but every one of them was already in the database, so 0 new rows were added. The existing rows were refreshed.": "Acquisizione completata: sono state raccolte %d offerte ma erano tutte già presenti nel database, quindi sono state aggiunte 0 nuove righe. Le righe esistenti sono state aggiornate.",
        "Interactive browser opened. Please complete the human-verification / captcha if prompted in the Chromium window...": "Browser interattivo aperto. Completa la verifica umana / captcha se richiesto nella finestra di Chromium...",
        "Job Role / Category Distribution (Functional Profession)": "Distribuzione per ruolo / categoria professionale",
        "Jobs by Granular Category (All Categories)": "Offerte per categoria dettagliata (Tutte le categorie)",
        "Jobs by Granular Category": "Offerte per categoria dettagliata",
        " (All Categories)": " (Tutte le categorie)",
        " (Filtered: %s)": " (Filtrato: %s)",
        "Skill": "Competenza",
        "Other": "Altro",
        "Reloc Yes": "Trasfer. Sì",
        "Source:": "Fonte:",
        "Target URL": "URL di destinazione",
        "Total": "Totale",
        "Total Jobs": "Offerte totali",
        "Visa ?": "Visto ?",
        "Visa Yes": "Visto Sì",
        "e.g. Robert Walters IE": "es. Robert Walters IE",
        "https://www.robertwalters.ie/jobs.html": "https://www.robertwalters.ie/jobs.html",
        "Please provide the portal careers URL.": "Fornisci l'URL delle carriere del portale.",
        "Please specify a portal URL.": "Specifica un URL del portale.",
        "Missing URL": "URL mancante",
        "Ingestion Complete": "Acquisizione completata",
        "Successfully harvested and ingested %d jobs directly into the database!": "Acquisite e inserite con successo %d offerte direttamente nel database!",
        "Launching interactive browser session... solve any captcha when prompted.": "Avvio sessione browser interattiva... risolvi il captcha se richiesto.",
        "Load & Ingest": "Carica e acquisisci",
        "Recently Blocked / Known Bot-Guarded Portals": "Portali recentemente bloccati / protetti da bot",
        "Launch Assisted Browser & Ingest": "Avvia browser assistito e acquisisci",
        "Portal Careers URL:": "URL carriera del portale:",
        "Company / Seed Name:": "Nome azienda / seed:",
        "Launch Assisted Ingestion for Portal": "Avvia acquisizione assistita per portale",
        "Certain portals enforce Cloudflare, PerimeterX, or bot verification (e.g. 'Press & Hold'). Use this tab to open an interactive Chromium window, complete human verification, and automatically harvest all vacancy pages directly into SponsorScout's database.": "Alcuni portali impongono Cloudflare, PerimeterX o verifiche bot (es. 'Premi e tieni premuto'). Usa questa scheda per aprire una finestra interattiva di Chromium, completare la verifica umana e acquisire automaticamente tutte le offerte direttamente nel database di SponsorScout.",
        "Assisted Browser Ingestion": "Acquisizione con browser assistito",
        "Re-Analysis Failed": "Rianalisi fallita",
        "Successfully re-analyzed %d jobs with updated granular categories and skills!": "Rianalizzati con successo %d lavori con categorie e competenze dettagliate!",
        "Re-Analysis Complete": "Rianalisi completata",
        "Extract updated skills and granular categories across all existing jobs in the database": "Estrai competenze aggiornate e categorie granulari su tutti i lavori esistenti nel database",
        "Re-Analyze Database Skills": "Rianalizza competenze nel database",
        "Top In-Demand Skills & Tools (From Titles & JDs)": "Competenze e strumenti più richiesti (da titoli e descrizioni)",
        "Jobs by Granular Category (From Job Title)": "Lavori per categoria dettagliata (dal titolo)",
        "Assisted Ingestion": "Acquisizione assistita",
        "* Insights reflect scanned job portals in SponsorScout's database, not the entire employment market.": "* Gli approfondimenti riflettono i portali scansionati nel database di SponsorScout, non l'intero mercato del lavoro.",
        "No data available": "Nessun dato disponibile",
        "Sponsorship by Category": "Sponsorizzazioni per categoria",
        "Employer Domains (From Seed Industry)": "Settori dei datori di lavoro (dal settore seed)",
        "Top In-Demand Skills (From Job Descriptions)": "Competenze più richieste (dalle descrizioni)",
        "Jobs by Category (From Job Title)": "Lavori per categoria (dal titolo del lavoro)",
        "Categorized: %d / %d (%s%%) | With Skills: %d (%s%%)": "Classificati: %d / %d (%s%%) | Con competenze: %d (%s%%)",
        "Coverage: --": "Copertura: --",
        "Aggregators / Agencies Only": "Solo aggregatori / agenzie",
        "Direct Employers Only": "Solo datori di lavoro diretti",
        "All Sources": "Tutte le fonti",
        "All Countries": "Tutti i paesi",
        "Filters & Coverage": "Filtri e Copertura",
        "Insights": "Approfondimenti",
        "Assisted ingestion finished: %d job(s) added directly to Search tab.": "Acquisizione assistita completata: %d offerte aggiunte direttamente alla scheda Cerca.",
        "Assisted Ingestion Complete": "Acquisizione assistita completata",
        "Open in External Browser": "Apri nel browser esterno",
        "In-App Assisted Ingest": "Acquisizione assistita integrata",
        "Would you like to resolve them using the in-app assisted browser?": "Vuoi risolverli utilizzando il browser assistito integrato?",
        # ── Header ─────────────────────────────────────────────────────
        "Verified sponsorship-focused jobs from official career pages and ATS boards":
            "Lavori verificati da pagine carriera ufficiali e bacheche ATS - Per Hamliee ❤ !!",
        "Ready": "Pronto",
        "Scan complete.": "Scansione completata.",

        # ── Tab names ──────────────────────────────────────────────────
        "Search": "Cerca",
        "Dashboard": "Pannello",
        "Applications": "Candidature",
        "ATS Health": "Stato ATS",
        "AI Tailor": "AI Personalizza",
        "Tools": "Strumenti",

        # ── Search tab ─────────────────────────────────────────────────
        "Title:": "Posizione:",
        "Company:": "Azienda:",
        "Country:": "Paese:",
        "Sponsorship:": "Sponsorizzazione:",
        "Remote:": "Remoto:",
        "Experience:": "Esperienza:",
        "Experience": "Esperienza",
        "No experience requirement found in the job description": "Nessun requisito di esperienza indicato nella descrizione del lavoro",
        "The ad mentions experience without stating a figure or a level.": "L'annuncio cita l'esperienza senza indicare né cifre né livello.",
        "Seniority level \"{level}\" — the ad states no number of years.": "Livello di seniority \"{level}\" — l'annuncio non indica un numero di anni.",
        # Tooltip dei verdetti (Sponsor / Carta Blu / Ricolloc.): cita la frase
        # che ha prodotto Y / N / ?, così l'utente può verificare la valutazione
        # senza aprire l'annuncio. "Unknown" è spiegato esplicitamente perché una
        # "?" accanto a una "Y" sembrerebbe un bug di rendering anziché
        # un'assenza di dichiarazione.
        "Confidence: {c:.0%}": "Affidabilità: {c:.0%}",
        "Found in the job description:": "Trovato nella descrizione del lavoro:",
        "{col}: {v}\n\nThe job description makes no explicit statement about this. Unknown means the ad did not say — it is not a \"no\".":
            "{col}: {v}\n\nLa descrizione del lavoro non contiene alcuna dichiarazione esplicita al riguardo. \"Unknown\" significa che l'annuncio non lo dice: non è un \"no\".",
        "{col}: {v}\n\nNo supporting sentence was recorded for this verdict.":
            "{col}: {v}\n\nNessuna frase di supporto è stata registrata per questa valutazione.",
        "Job title": "Posizione",
        "Location": "Località",
        "Copy URL": "Copia URL",
        "Job saved to Applications.": "Lavoro salvato nelle Candidature.",
        "Blue Card": "Carta Blu",
        "Reloc": "Ricollocaz.",
        "Sponsor": "Sponsor",
        "Possible": "Possibile",
        "DNS re-crawl: %s host(s) recovering": "Ri-scansione DNS: %s host in ripresa",
        "Run:": "Esegui:",
        "Hedged offers: the posting says it sponsors, but with conditions (\"however we are not sponsoring this role\"). Not counted as Yes.": "Offerte condizionate: l'annuncio dichiara sponsorizzazione, ma con condizioni (\"however we are not sponsoring this role\"). Non conta come Sì.",
        "SponsorScout": "SponsorScout",
        "Sort:": "Ordina:",
        "Objective:": "Obiettivo:",
        "Balanced": "Bilanciato",
        "Strict quality": "Qualità rigorosa",
        "Visa sponsor": "Sponsor visto",
        "Local EU": "UE locale",
        "Blue Card focus": "Focus Blue Card",
        "EU Blue Card": "Carta Blu UE",
        "Relocation": "Ricollocazione",
        "All": "Tutti",
        "Regex": "Regex",
        "Enable regular-expression matching in Title / Company / Location filters (e.g. ^senior (backend|platform)$).":
            "Abilita la corrispondenza con espressioni regolari nei filtri Posizione / Azienda / Località (es. ^senior (backend|platform)$).",
        "Invalid regular expression": "Espressione regolare non valida",
        "Regex disabled — invalid pattern:\n{error}":
            "Regex disabilitato — pattern non valido:\n{error}",
        "Any (incl. unknown)": "Qualsiasi (incl. sconosciuto)",
        "Unknown / Not classified": "Sconosciuto / Non classificato",
        "Intern": "Stage",
        "Entry": "Junior",
        "Mid": "Intermedio",
        "Senior": "Senior",
        "Lead": "Capo",
        "Exec": "Dirigente",
        "Best match": "Miglior corrispondenza",
        "Latest": "Più recenti",
        "Sponsored Only": "Solo Sponsorizzati",
        "Remote EU": "Remoto UE",
        "Remote EMEA": "Remoto EMEA",
        "Remote Global": "Remoto Globale",
        "Remote Only": "Solo Remoto",
        "Hybrid": "Ibrido",
        "jobs found": "lavori trovati",
        "job found": "lavoro trovato",

        # ── AI Rating panel ────────────────────────────────────────────
        "AI Job Rating & Eligibility": "Valutazione AI ed Eleggibilità Lavoro",
        "Tailor CV & Letter": "Personalizza CV e Lettera",
        "No job selected.": "Nessun lavoro selezionato.",
        "Rating against your saved CV profile":
            "Valutazione basata sul tuo CV salvato",
        "Rating will use your saved CV profile":
            "La valutazione userà il tuo CV salvato",

        # ── Search: right-click context menu ───────────────────────────
        "Open in browser": "Apri nel browser",
        "Save to Applications": "Salva nelle Candidature",
        "Tailor CV & Cover Letter": "Personalizza CV e Lettera",

        # ── Dashboard ──────────────────────────────────────────────────
        "Companies": "Aziende",
        "Verified Jobs": "Lavori Verificati",
        "Sponsored": "Sponsorizzati",
        "Remote": "Remoti",
        "New this week": "Nuovi questa settimana",
        "Top companies by sponsorship score":
            "Migliori aziende per punteggio di sponsorizzazione",
        "Jobs by country": "Lavori per paese",

        # ── Applications tab ───────────────────────────────────────────
        "Saved applications": "Candidature salvate",
        "Refresh": "Aggiorna",
        "Remove Selected": "Rimuovi Selezionato",
        "Edit selected": "Modifica selezionato",
        "Status:": "Stato:",
        "Notes:": "Note:",
        "saved": "salvato",
        "applied": "candidatura inviata",
        "interview": "colloquio",
        "offer": "offerta",
        "rejected": "rifiutato",

        # ── ATS Health tab ─────────────────────────────────────────────
        "ATS Connector Health": "Stato Connettore ATS",
        "Success/failure rates per connector after each scan.":
            "Tasso di successo/errore per connettore dopo ogni scansione.",

        # ── Tools tab ──────────────────────────────────────────────────
        "Scanner": "Scansione",
        "Scan all 111 companies via their official ATS APIs.":
            "Scansiona tutte le 111 aziende tramite le loro API ATS ufficiali.",
        "idle": "inattivo",
        "Scan Now": "Scansiona Ora",
        "Auto (1 h)": "Automatico (1 h)",
        "Stop": "Ferma",
        "Pause": "Pausa",
        "Showing {a}–{b} of {n} results":
            "Mostrati {a}–{b} di {n} risultati",
        "Previous page": "Pagina precedente",
        "Next page": "Pagina successiva",
        "Rows per page": "Righe per pagina",
        "Scan Log:": "Registro Scansione:",
        "Data Quality": "Qualità Dati",
        "Remove duplicate jobs and companies from the database.":
            "Rimuovi lavori e aziende duplicati dal database.",
        "Clear Scan Data": "Cancella Dati di Scansione",
        "The database already contains no scanned data.":
            "Il database non contiene dati scansionati.",
        "This will permanently delete {jobs} scanned job(s), {runs} scan run(s) and {logs} scan log row(s) from the database.\nSeed CSVs and saved applications are NOT affected. Continue?":
            "Questo eliminerà permanentemente {jobs} lavoro/i scansionato/i, {runs} esecuzione/i di scansione e {logs} riga/i di registro dal database.\nI CSV semina e le candidature salvate NON saranno influenzati. Continuare?",
        "Scan data cleared": "Dati di Scansione Cancellati",
        "Removed {jobs} job(s), {runs} scan run(s) and {logs} scan log row(s). Seed CSVs were not touched.":
            "Rimosso {jobs} lavoro/i, {runs} esecuzione/i di scansione e {logs} riga/i di registro. I CSV semina non sono stati toccati.",
        "Stale data cleared": "Dati Obsoleti Cancellati",
        "Removed {n} expired job(s) from the database.":
            "Rimosso {n} lavoro/i scaduto/i dal database.",
        "No expired jobs to remove — the database is already clean.":
            "Nessun lavoro scaduto da rimuovere — il database è già pulito.",
        "View Per-Company Log": "Vedi Registro per Azienda",
        "Download Scan Log": "Scarica Registro di Scansione",
        "Select a scan run first.": "Seleziona prima un'esecuzione di scansione.",
        "Scan log downloaded": "Registro di scansione scaricato",
        "Scan log saved to:\n{path}": "Registro di scansione salvato in:\n{path}",
        "CSV files (*.csv);;All files (*.*)": "File CSV (*.csv);;Tutti i file (*.*)",
        "Could not save scan log:\n{error}": "Impossibile salvare il registro di scansione:\n{error}",
        "The file appears empty:\n{path}": "Il file appare vuoto:\n{path}",
        "Scan log ({bytes} bytes, {rows} company row(s), {events} event(s)) saved to:\n{path}":
            "Registro di scansione ({bytes} byte, {rows} riga/i aziendale/i, {events} evento/i) salvato in:\n{path}",
        "Run Dedup": "Esegui Deduplicazione",
        "AI Settings": "Impostazioni AI",
        "AI provider API key + prompts for job rating, eligibility (uses your CV from AI Tailor tab), and document generation.":
            "Chiave API del provider AI + prompt per valutazione lavori, eleggibilità (usa il CV dalla scheda AI Personalizza) e generazione documenti.",
        "AI API Key / Token:": "Chiave API / Token AI:",
        "Save Key": "Salva Chiave",
        "Provider:": "Provider:",
        "Model:": "Modello:",
        "(type any model name; chips are provider-specific)":
            "(inserisci qualsiasi nome modello; i suggerimenti sono specifici del provider)",
        "Base URL:": "URL Base:",
        "(auto-filled for built-in providers; required for custom)":
            "(compilato automaticamente per provider integrati; obbligatorio per personalizzato)",
        "type the exact model ID from your provider":
            "inserisci l'ID esatto del modello dal tuo provider",
        "Job Rating & Eligibility Prompt  (AI uses this + your CV to score each job):":
            "Prompt Valutazione ed Eleggibilità Lavoro  (l'AI usa questo + il tuo CV per valutare ogni lavoro):",
        "Save Prompt": "Salva Prompt",
        "Reset to Default": "Ripristina Predefinito",
        "CV Tailoring Prompt  (how AI rewrites your CV to match a JD):":
            "Prompt Personalizzazione CV  (come l'AI riscrive il tuo CV per corrispondere a un annuncio):",
        "Save CV Prompt": "Salva Prompt CV",
        "Reset": "Ripristina",
        "Cover / Motivation Letter Prompt  (for EU-based roles — personalised from your CV + JD):":
            "Prompt Lettera di Presentazione  (per ruoli nell'UE — personalizzata dal tuo CV + annuncio):",
        "Save Letter Prompt": "Salva Prompt Lettera",
        "Company Discovery": "Scoperta Aziende",
        "Probes 210 curated ATS boards.  Use role keywords: 'analyst', 'engineer', 'backend', 'data'.":
            "Indaga 210 bacheche ATS curate. Usa parole chiave: 'analista', 'ingegnere', 'backend', 'dati'.",
        "Query:": "Ricerca:",
        "Discover": "Scopri",
        "Freshness Check": "Verifica Aggiornamento",
        "Verify jobs still exist online — auto-expires dead links.":
            "Verifica che i lavori esistano ancora online — scadenza automatica dei link morti.",
        "Max jobs:": "Max lavori:",
        "Run": "Esegui",

        # ── AI Tailor tab ──────────────────────────────────────────────
        "AI CV & Cover Letter Tailor": "AI Personalizzazione CV e Lettera di Presentazione",
        "Select a job in Search → 'Tailor CV & Letter', or load a JD manually below":
            "Seleziona un lavoro in Cerca → 'Personalizza CV e Lettera', o carica un annuncio manualmente",
        "No job selected — use Search tab or paste a JD below":
            "Nessun lavoro selezionato — usa la scheda Cerca o incolla un annuncio",
        "Job URL:": "URL Lavoro:",
        "Fetch JD": "Scarica Annuncio",
        "— or paste full JD below —": "— oppure incolla l'annuncio completo qui sotto —",
        "Use this JD": "Usa questo Annuncio",
        "My CV (stored locally)": "Il mio CV (salvato localmente)",
        "Paste your current CV once — it's saved for all future tailoring sessions.":
            "Incolla il tuo CV una volta — viene salvato per tutte le future sessioni di personalizzazione.",
        "Save CV": "Salva CV",
        "CV saved": "CV salvato",
        "Base Cover Letter (template for AI)":
            "Lettera di Presentazione Base (modello per l'AI)",
        "Paste an example cover letter you like. The AI will match its style/tone when generating new letters.":
            "Incolla una lettera di presentazione che ti piace. L'AI ne riprodurrà lo stile/tono quando genererà nuove lettere.",
        "Save Template": "Salva Modello",
        "Template saved": "Modello salvato",
        "Generate": "Genera",
        "Tailor My CV": "Personalizza il mio CV",
        "Write Cover Letter": "Scrivi Lettera di Presentazione",
        "Both": "Entrambi",
        "Result": "Risultato",
        "CV": "CV",
        "Cover Letter": "Lettera di Presentazione",
        "Copy": "Copia",
        "Fetching…": "Scaricamento…",

        # ── Messages: tailor tab ───────────────────────────────────────
        "Empty JD": "Annuncio vuoto",
        "Paste a job description first.":
            "Incolla prima una descrizione del lavoro.",
        "No URL": "Nessun URL",
        "Enter a job post URL first.":
            "Inserisci prima un URL dell'annuncio di lavoro.",
        "Empty CV": "CV vuoto",
        "Paste your CV first.":
            "Incolla prima il tuo CV.",
        "No Job Description": "Nessun Annuncio",
        "Fetch or paste a job description first, then click 'Use this JD'.":
            "Scarica o incolla prima un annuncio, poi clicca 'Usa questo Annuncio'.",
        "No CV": "Nessun CV",
        "Paste your CV in the 'My CV' box and save it first.":
            "Incolla il tuo CV nella sezione 'Il mio CV' e salvalo prima.",
        "Copied to clipboard!": "Copiato negli appunti!",
        "Select a row": "Seleziona una riga",
        "Click a row first, then click Remove.":
            "Clicca prima su una riga, poi su Rimuovi.",
        "Remove": "Rimuovi",
        "Search error": "Errore di ricerca",
        "Error": "Errore",
        "Missing": "Manca",
        "Enter a search query.":
            "Inserisci una ricerca.",

        # ── Messages: AI settings ──────────────────────────────────────
        "AI API key saved.": "Chiave API AI salvata.",
        "Empty": "Vuoto",
        "Prompt cannot be empty.": "Il prompt non può essere vuoto.",
        "Custom prompt saved.": "Prompt personalizzato salvato.",
        "CV tailoring prompt saved.": "Prompt personalizzazione CV salvato.",
        "Cover letter prompt saved.": "Prompt lettera di presentazione salvato.",
        "Reset prompt to default?":
            "Ripristinare il prompt predefinito?",
        "Reset CV tailoring prompt to default?":
            "Ripristinare il prompt di personalizzazione CV predefinito?",
        "Reset cover letter prompt to default?":
            "Ripristinare il prompt della lettera di presentazione predefinito?",
        "Unknown provider": "Provider sconosciuto",
        "Missing model": "Modello mancante",
        "Type a model name first.":
            "Inserisci prima il nome di un modello.",
        "Missing base URL": "URL base mancante",

        # ── Messages: first run ────────────────────────────────────────
        "Welcome to SponsorScout": "Benvenuto in SponsorScout",

        # ── Messages: general ──────────────────────────────────────────
        "No job selected": "Nessun lavoro selezionato",
        "Select a job in the Search tab first.":
            "Seleziona prima un lavoro nella scheda Cerca.",
        "Dashboard data could not be loaded.":
            "Impossibile caricare i dati del pannello.",
        "Dedup complete": "Deduplicazione completata",
        "Probing ATS boards for": "Indagine bacheche ATS per",
        "Found": "Trovati",
        "candidate(s).": "candidato/i.",
        "No new companies found.":
            "Nessuna nuova azienda trovata.",
        "Try: 'analyst', 'backend', 'data engineer'":
            "Prova: 'analista', 'backend', 'ingegnere dati'",
        "Discovery done.": "Scoperta completata.",
        "Discovery failed.": "Scoperta fallita.",
        "Running discovery…": "Scoperta in corso…",
        "running…": "in esecuzione…",
        "Scanning…": "Scansione in corso…",
        "auto — every 1 h": "automatico — ogni 1 h",
        "stopped": "fermato",
        "Failed.": "Fallito.",
        "Verified": "Verificato",

        # ── Tooltip: How to Use ────────────────────────────────────────
        "AI Tailor — How to Use": "AI Personalizza — Come Usare",

        # ── Tooltip: JD section ────────────────────────────────────────

        # ── Tooltip: CV section ────────────────────────────────────────
        "My CV": "Il mio CV",

        # ── Tooltip: Cover Letter section ──────────────────────────────
        "Base Cover Letter Template": "Modello Lettera di Presentazione Base",

        # ── Tooltip: Generate section ──────────────────────────────────
        "Generate Buttons": "Pulsanti di Generazione",

        # ── Tooltip: Result section ────────────────────────────────────
        "Result Area": "Area Risultati",

        # ── AI Assistant tab ────────────────────────────────────────────
        "How to use": "Come usare",

        # ── Language toggle ────────────────────────────────────────────

        # ── AI / Search extras ───────────────────────────────────────
        "Contacting Gemini API...": "Contatto API Gemini...",
        "Contacting custom AI endpoint...": "Contatto endpoint AI personalizzato...",
        "Contacting {provider} OpenAI-compatible API...": "Contatto API {provider} compatibile OpenAI...",
        "Job Description": "Descrizione del lavoro",
        "No CV on file — paste yours in AI Tailor tab for personalised results":
            "Nessun CV salvato — incolla il tuo nella scheda AI Personalizza per risultati personalizzati",
        "Rate this job": "Valuta questo lavoro",
        "Rate with AI": "Valuta con AI",
        "Saved": "Salvato",
        "Select a job, then click 'Rate this job'.": "Seleziona un lavoro, poi clicca 'Valuta questo lavoro'.",

        # ── Tools tab extras ─────────────────────────────────────────
        "Scan History": "Storico Scansioni",
        "Idle": "Inattivo",
# ── Dashboard ──────────────────────────────────────────────────
        "Total Companies": "Aziende Totali",
        "Sponsored Jobs": "Lavori Sponsorizzati",
        "Remote Jobs": "Lavori Remoti",
        "Top Companies by Sponsorship": "Migliori Aziende per Sponsorizzazione",
        "Jobs by Country": "Lavori per Paese",
        "Company": "Azienda",
        "Country": "Paese",
        "Jobs": "Lavori",
        "Top Sponsor": "Miglior Sponsor",
        "Rescan Companies": "Riscansiona Aziende",
        "Unknown": "Sconosciuto",

        # ── Tools descriptions ────────────────────────────────────────
        "Scanner description": "Avvia una scansione lavori su tutte le aziende seminate — ogni board ATS e pagina carriere viene scansionata, poi ogni lavoro viene arricchito dalla sua pagina di dettaglio. L'output live appare qui sotto.",
        "Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.":
            "Scansiona ogni azienda seminata (board ATS + pagine carriere) e arricchisce ogni lavoro dalla sua pagina di dettaglio, così nessun annuncio resta senza evidenza.",
        "Custom Scan": "Scansione Personalizzata",
        "Custom scan started": "Scansione personalizzata avviata",
        "Starting custom scan…": "Avvio scansione personalizzata…",
        "Choose specific companies and/or source types (ATS and/or career portals) to scan instead of every seeded company.":
            "Scegli aziende specifiche e/o tipi di fonte (ATS e/o pagine carriere) da scansionare invece di tutte le aziende seminate.",
        "Choose the source types and companies to scan. Selected companies are scanned exactly like in a full scan — the full scan simply covers every seeded company.":
            "Scegli i tipi di fonte e le aziende da scansionare. Le aziende selezionate vengono scansionate esattamente come in una scansione completa — la scansione completa copre semplicemente tutte le aziende seminate.",
        "Scan ATS portals (API-based, fast)": "Scansiona i portali ATS (basati su API, veloce)",
        "Scan career portals (crawled, slower)": "Scansiona i portali carriere (crawled, più lento)",
        "Filter companies…": "Filtra aziende…",
        "Select all": "Seleziona tutto",
        "Clear": "Azzera",
        "Start Custom Scan": "Avvia Scansione Personalizzata",
        "Selected: {parts}": "Selezionati: {parts}",
        "ATS: {n}/{total}": "ATS: {n}/{total}",
        "Career: {n}/{total}": "Carriere: {n}/{total}",
        "Select at least one source type.": "Seleziona almeno un tipo di fonte.",
        "Select at least one company.": "Seleziona almeno un'azienda.",
        "Could not open the custom scan dialog.": "Impossibile aprire la finestra di scansione personalizzata.",
        "Scan History description": "Ogni scansione passata. Seleziona una riga per visualizzare o scaricare il suo registro per azienda con errori.",
        "Resume": "Riprendi",
        "Continue the last stopped scan — only companies it did not finish are scanned, so no progress is lost.":
            "Continua l'ultima scansione interrotta — vengono scansionate solo le aziende non completate, nessun progresso va perso.",
        "Stop the scan now and keep everything found so far. Press Resume later to continue the remaining companies — all browsers close, so other apps run smoothly again.":
            "Ferma ora la scansione mantenendo tutto ciò che è stato trovato. Premi Riprendi più tardi per continuare le aziende restanti — tutti i browser si chiudono, così le altre app tornano fluide.",
        "Pause the running scan in place — workers stop at the next company and browsers wait. Press Resume to continue instantly, no new scan is started.":
            "Metti in pausa la scansione in corso — i worker si fermano alla prossima azienda e i browser attendono. Premi Riprendi per continuare subito, senza avviare una nuova scansione.",
        "Continue the paused scan where it stopped — same run, no loss.":
            "Continua la scansione in pausa da dove si è fermata — stessa esecuzione, nessuna perdita.",
        "Stop the scan now and keep everything found so far. The stopped run is checkpointed — Resume (the other button) starts a new scan for the companies that were not finished, even after an app restart.":
            "Ferma ora la scansione mantenendo tutto ciò che è stato trovato. L'esecuzione interrotta viene salvata — Riprendi (l'altro pulsante) avvia una nuova scansione solo per le aziende non finite, anche dopo un riavvio dell'app.",
        "Looking up the last stopped scan…":
            "Ricerca dell'ultima scansione interrotta…",
        "Paused": "In pausa",
        "Pausing…": "Pausa in corso…",
        "Stopping…": "Arresto in corso…",
        "Stopped.": "Fermata.",
        "Stop the running scan? Everything found so far is kept and can be resumed later.":
            "Fermare la scansione in corso? Tutto ciò che è stato trovato viene mantenuto e potrà essere ripreso più tardi.",
        "Resuming scan": "Ripresa scansione",
        "Nothing to resume — no stopped scan with unfinished companies.":
            "Nulla da riprendere — nessuna scansione interrotta con aziende incompiute.",
        "Could not find a scan to resume:\n{error}":
            "Impossibile trovare una scansione da riprendere:\n{error}",
        "Resuming {run} — {done}/{total} companies already done, {remaining} remaining.":
            "Ripresa {run} — {done}/{total} aziende già fatte, {remaining} restanti.",
        "(+{n} companies added to seeds since the stop — they are included.)":
            "(+{n} aziende aggiunte ai seed dopo lo stop — sono incluse.)",
        "Resuming — {done}/{total} done.":
            "Ripresa — {done}/{total} fatte.",
        "Resume {run} — {done}/{total} done, {remaining} remaining.":
            "Riprendi {run} — {done}/{total} fatte, {remaining} restanti.",
        "resumed": "ripresa",
        "resumed from": "ripresa da",
        "Starting scan…": "Avvio scansione…",
        "ATS": "ATS",
        "Career": "Carriere",
        "Scan": "Scansione",
        "Finished.": "Finita.",
        "Cancelled — partial progress shown.": "Annullata — avanzamento parziale mostrato.",
        "Data Quality description": "Esamina gli alias affidabili degli annunci con backup e audit prima delle unioni; cancella esplicitamente i lavori scaduti o i dati scansionati.",
        "Freshness Check description": "Riverifica i lavori salvati rispetto alle loro pagine live e segna le scadute.",

        # ── Complete UI coverage (tools/data/apps/dashboard + dialogs) ──
        # Every literal key used anywhere in the app must exist here so the
        # Italian UI never falls back to English. Guarded by
        # tests/test_i18n_parity.py::test_every_ui_key_has_italian_translation.
        " Edit selected ": " Modifica selezionato ",
        "A scan is already running.": "Una scansione è già in corso.",
        "A source with this name and URL already exists.": "Esiste già una fonte con questo nome e questo URL.",
        "ATS / source type": "ATS / tipo di fonte",
        "ATS portals": "Portali ATS",
        "Add source": "Aggiungi fonte",
        "Add…": "Aggiungi…",
        "Advanced (optional)": "Avanzate (facoltativo)",
        "All reasons": "Tutti i motivi",
        "Application updated.": "Candidatura aggiornata.",
        "Latest notes": "Ultime note",
        "AI Tailoring": "Adattamento IA",
        "AI prompt": "Prompt IA",
        "Copied to clipboard ({n} characters).": "Copiato negli appunti ({n} caratteri).",
        "Copy prompt + JD + CV + CL": "Copia prompt + JD + CV + CL",
        "Could not connect to the site ({err}).": "Impossibile connettersi al sito ({err}).",
        "Could not read the job description": "Impossibile leggere la descrizione del lavoro",
        "Cover letter (optional)": "Lettera di presentazione (facoltativa)",
        "If a link is given, it is read. Otherwise the selected job is used.": "Se è inserito un link, viene letto. Altrimenti si usa il lavoro selezionato.",
        "Job description": "Descrizione del lavoro",
        "Job description (session only)": "Descrizione del lavoro (solo sessione)",
        "Job description loaded.": "Descrizione del lavoro caricata.",
        "Load JD": "Carica JD",
        "Load a job, or paste the job description here.": "Carica un lavoro oppure incolla qui la descrizione.",
        "Loading…": "Caricamento…",
        "No job chosen": "Nessun lavoro scelto",
        "Nothing to copy yet": "Niente da copiare",
        "Paste any job description link": "Incolla un link alla descrizione del lavoro",
        "Paste your CV here.": "Incolla qui il tuo CV.",
        "Paste your cover letter here (optional).": "Incolla qui la tua lettera di presentazione (facoltativa).",
        "Saved.": "Salvato.",
        "Select a job in the Search tab": "Seleziona un lavoro nella scheda Ricerca",
        "Select a job in the Search tab, or paste a job description link here.": "Seleziona un lavoro nella scheda Ricerca, oppure incolla qui un link alla descrizione.",
        "Selected job:": "Lavoro selezionato:",
        "The link is not a web page (type: {t}).": "Il link non è una pagina web (tipo: {t}).",
        "The page has too little readable text. It may need a login or only load with JavaScript.": "La pagina ha troppo poco testo leggibile. Potrebbe richiedere un accesso o caricarsi solo con JavaScript.",
        "The site answered HTTP {code}. It may be blocking automated reads.": "Il sito ha risposto HTTP {code}. Potrebbe bloccare le letture automatiche.",
        "The site did not answer in time.": "Il sito non ha risposto in tempo.",
        "These parts are empty: {parts}": "Queste parti sono vuote: {parts}",
        "This is not a web address. It must start with http:// or https://.": "Non è un indirizzo web. Deve iniziare con http:// o https://.",
        "Unexpected error: {e}": "Errore imprevisto: {e}",
        "Write the instructions for the AI here.": "Scrivi qui le istruzioni per l'IA.",
        "or JD URL:": "oppure URL JD:",
        "{reason}\n\nPlease paste the job description into the JD box.": "{reason}\n\nIncolla la descrizione del lavoro nel riquadro JD.",
        "Activity log": "Registro attività",
        "Status changes you save appear here.": "Le modifiche di stato salvate appaiono qui.",
        "{old} → {new}": "{old} → {new}",
        "status {s} (unchanged)": "stato {s} (invariato)",
        "Artifact:": "Artefatto:",
        "Cancel": "Annulla",
        "Cannot save": "Impossibile salvare",
        "Career portals": "Portali carriere",
        "Careers URL": "URL carriera",
        "Check up to": "Controlla fino a",
        "Checked {checked} — expired {expired}.": "Controllati {checked} — scaduti {expired}.",
        "Clear Stale Data": "Cancella Dati Obsoleti",
        "Company name": "Nome azienda",
        "Data Management": "Gestione Dati",
        "Delete": "Elimina",
        "Delete '{}' from this seed file? (Not written until you save.)":
            "Eliminare '{}' da questo file seed? (Non scritto finché non salvi.)",
        "Delete source": "Elimina fonte",
        "Discard all edits and restore the seed file shipped with the application.":
            "Scarta tutte le modifiche e ripristina il file seed incluso nell'applicazione.",
        "Discard all edits and restore the seed file shipped with the application?":
            "Scartare tutte le modifiche e ripristinare il file seed incluso nell'applicazione?",
        "Duplicate source": "Fonte duplicata",
        "Dups": "Dup",
        "Edit source": "Modifica fonte",
        "Edit…": "Modifica…",
        "Errors": "Errori",
        "File:": "File:",
        "Freshness check done.": "Controllo aggiornamento completato.",
        "Industry": "Settore",
        "Invalid data": "Dati non validi",
        "Language: ": "Lingua: ",
        "Load": "Carica",
        "Maximum number of active jobs to re-verify per run.":
            "Numero massimo di lavori attivi da riverificare per esecuzione.",
        "Method": "Metodo",
        "No bundled default file is available.": "Nessun file predefinito incluso disponibile.",
        "No quarantine artifacts found.": "Nessun artefatto in quarantena trovato.",
        "No selection": "Nessuna selezione",
        "Promote Selected to Jobs": "Porta selezionati nei lavori",
        "Quarantine": "Quarantena",
        "Quarantine Review": "Revisione Quarantena",
        "Quarantined": "In quarantena",
        "Reason:": "Motivo:",
        "Reload": "Ricarica",
        "Reset failed": "Ripristino fallito",
        "Reset to bundled defaults": "Ripristina i valori predefiniti inclusi",
        "Review Quarantine": "Rivedi Quarantena",
        "Row {} has problems:": "La riga {} ha problemi:",
        "Run ID": "ID esecuzione",
        "Running…": "In esecuzione…",
        "Save": "Salva",
        "Save to CSV": "Salva in CSV",
        "Saved on": "Salvato il",
        "Scan finished: ": "Scansione terminata: ",
        "Scan output appears here…": "L'output della scansione appare qui…",
        "Scan started": "Scansione avviata",
        "Scan stopped.": "Scansione fermata.",
        "Scope policy": "Politica di ambito",
        "Posted": "Pubblicato",
        "Seed files changed — they will be used on the next scan.":
            "File seed modificati — verranno usati alla prossima scansione.",
        "Select a row to delete first.": "Seleziona prima una riga da eliminare.",
        "Select a row to edit first.": "Seleziona prima una riga da modificare.",
        "Select rows first.": "Seleziona prima delle righe.",
        "Source type": "Tipo di fonte",
        "Started": "Avviata",
        "Status": "Stato",
        "Title": "Titolo",
        "URL": "URL",
        "Verifying up to {n} jobs…": "Verifica di fino a {n} lavori…",
        "jobs": "lavori",
        "{n} jobs ingested": "{n} lavori acquisiti",
        "{n} row(s) promoted into jobs.": "{n} riga/e promosse nei lavori.",
        "{n} already in jobs (updated).": "{n} già nei lavori (aggiornati).",
        "{n} skipped (missing URL/title or error).": "{n} saltate (URL/titolo mancante o errore).",
        "Close this dialog to refresh the Search list.": "Chiudi questa finestra per aggiornare l'elenco di ricerca.",
        "Errors:": "Errori:",
        "…and {n} more.": "…e altre {n}.",
        "No value under cursor": "Nessun valore sotto il cursore",
        "Copy {header}": "Copia {header}",
        "Copy row": "Copia riga",
        "Open URL": "Apri URL",
        "Cell": "Cella",
        "{shown} of {total} quarantined rows.": "{shown} di {total} righe in quarantena.",
        "{} companies": "{} aziende",
        "{} companies written to\n{}": "{} aziende scritte in\n{}",
        "↻ Refresh": "↻ Aggiorna",
        "Remove  {title}  at  {company}?": "Rimuovere  {title}  presso  {company}?",
        "Removed {jobs} duplicate job(s) and {companies} duplicate company entry(ies).":
            "Rimosse {jobs} voci duplicate di lavori e {companies} voci duplicate di aziende.",
        # ── Data Management: seed-grid column labels, dialog fields ─────────
        # Keys are the title-cased column names (SeedRowDialog) and the
        # explicit labels of _column_label() (seed-grid headers).
        "Provider": "Provider",
        "Board Slug": "Board Slug",
        "Target Country": "Paese target",
        "Sponsorship History": "Storico sponsorship",
        "English Friendly": "English friendly",
        "Remote Score": "Punteggio remoto",
        "Notes": "Note",
        "Seed Name": "Nome seed",
        "Canonical Name": "Nome canonico",
        "Seed file updated": "File seed aggiornato",
        "This file uses a removed column ({}). It has been read correctly and will be written in the current format the next time you save.":
            "Questo file usa una colonna rimossa ({}). È stato letto correttamente e verrà riscritto nel formato corrente la prossima volta che salvi.",
        # ── Dashboard: empty-run panel + work-mode stats ────────────────────
        "No Jobs Found (last run)": "Nessun lavoro trovato (ultima esecuzione)",
        "Blocked portals": "Portali bloccati",
        "These portals block automated access, so the scan could not read them:": "Questi portali bloccano l'accesso automatico, quindi la scansione non ha potuto leggerli:",
        "Open them in your default browser to search manually?": "Vuoi aprirli nel browser predefinito per cercarli manualmente?",
        "Top Employers by Sponsorship": "Principali datori di lavoro per sponsorship",
        "Why": "Perché",
        "auto": "auto",
        "error": "errore",
        "hybrid": "ibrido",
        "no jobs": "nessun lavoro",
        "{remote} fully remote, {hybrid} hybrid. {unknown} job(s) state no work mode at all.":
            "{remote} full remote, {hybrid} ibridi. {unknown} lavori non indicano alcuna modalità di lavoro.",
        "Run {run}: {empty} of {total} scanned companies returned no jobs at all; {errors} of those failed with an error.":
            "Esecuzione {run}: {empty} aziende su {total} scansionate non hanno restituito alcun lavoro; {errors} di queste hanno fallito con un errore.",
        "Manage the source URLs scanned by SponsorScout.  ATS portals are scanned via their job-board APIs; career portals are crawled on the company site.  Edits are saved to your personal seed files and take effect on the next scan.":
            "Gestisci gli URL delle fonti scansionate da SponsorScout. I portali ATS vengono scansionati tramite le API dei job board; i portali carriere vengono crawpati sul sito dell'azienda. Le modifiche sono salvate nei tuoi file seed personali e hanno effetto alla prossima scansione.",
        "No data yet.\n\nRun the first scan now? It fetches jobs from each company's official career page and ATS board (1–3 minutes).":
            "Nessun dato ancora.\n\nAvviare ora la prima scansione? Recupera i lavori dalla pagina carriera e dal board ATS ufficiale di ogni azienda (1–3 minuti).",
    },
}


# Approved 2026-10-24 follow-up: persistent explicit queue, incremental Insights
# and backed-up existing-data maintenance. Placeholders remain identical.
_FOLLOWUP_IT = {
    "Blocked portals from your scans": "Portali bloccati dalle tue scansioni",
    "Cancel refresh": "Annulla aggiornamento",
    "Cancelling refresh... Saved batches are retained.": "Annullamento aggiornamento... I lotti salvati vengono conservati.",
    "Career Portal": "Portale carriere",
    "Company or portal name": "Nome dell'azienda o del portale",
    "Could not load blocked queue: {error}": "Impossibile caricare la coda dei portali bloccati: {error}",
    "Could not save queue outcome: {error}": "Impossibile salvare l'esito nella coda: {error}",
    "Direct employer": "Datore di lavoro diretto",
    "Dismissed": "Ignorato",
    "Existing-data cleanup": "Pulizia dei dati esistenti",
    "Failed": "Non riuscito",
    "Failed: {error}": "Non riuscito: {error}",
    "Finish ingestion before reviewing existing-data merges.": "Termina l'acquisizione prima di esaminare le unioni dei dati esistenti.",
    "Finished — partial results retained.": "Terminato — risultati parziali conservati.",
    "Insights error: {error}": "Errore Approfondimenti: {error}",
    "Insights up to date. Updated {count} jobs.": "Approfondimenti aggiornati. Aggiornate {count} offerte.",
    "Interrupted — retry explicitly": "Interrotto — riprova esplicitamente",
    "Manually requested assisted scan": "Scansione assistita richiesta manualmente",
    "Merge {rows} duplicate alias rows in {groups} trusted posting groups? A full SQLite backup and audit are saved first. Manual-country conflicts are skipped; applications are unchanged.": "Unire {rows} righe di alias duplicati in {groups} gruppi di annunci con identità affidabile? Prima vengono salvati un backup SQLite completo e un registro di audit. I conflitti fra paesi corretti manualmente vengono ignorati; le candidature non cambiano.",
    "Merged {count} alias rows.\nBackup: {backup}\nAudit: {audit}": "Unite {count} righe di alias.\nBackup: {backup}\nAudit: {audit}",
    "No safe alias merges found. Conflicting manual-country groups: {count}.": "Nessuna unione sicura di alias trovata. Gruppi con paesi manuali in conflitto: {count}.",
    "Opening interactive browser. Complete human verification if requested.": "Apertura del browser interattivo. Completa la verifica umana se richiesta.",
    "Partial": "Parziale",
    "Partial targets: {partial}; source/alias rows merged: {merged}": "Portali parziali: {partial}; righe di fonte/alias unite: {merged}",
    "Pending": "In attesa",
    "Please provide a valid HTTP or HTTPS portal URL.": "Inserisci un URL HTTP o HTTPS valido del portale.",
    "Processed {done} / {total} jobs": "Elaborate {done} / {total} offerte",
    "Re-extract all saved jobs? Previously committed results remain visible during the rebuild.": "Riestrarre tutte le offerte salvate? I risultati già confermati restano visibili durante la ricostruzione.",
    "Re-extract every saved job. Normal refresh processes only new or changed inputs.": "Riestrae ogni offerta salvata. L'aggiornamento normale elabora solo gli input nuovi o modificati.",
    "Reason / Phase": "Motivo / Fase",
    "Rebuild Insights": "Ricostruisci Approfondimenti",
    "Rebuild all": "Ricostruisci tutto",
    "Recruiter / agency": "Recruiter / agenzia",
    "Refresh cancelled. Committed batches are saved.": "Aggiornamento annullato. I lotti confermati sono salvati.",
    "Refresh queue": "Aggiorna coda",
    "Regular scans never open an interactive browser. Real verification-blocked portals from ATS and Career scans appear below and survive restart. Choose a portal explicitly, then complete any human verification in Chromium.": "Le scansioni normali non aprono mai un browser interattivo. I portali realmente bloccati da verifiche durante le scansioni ATS e Career compaiono qui sotto e restano dopo il riavvio. Scegli esplicitamente un portale, poi completa la verifica umana in Chromium.",
    "Resolved": "Risolto",
    "Review alias merges": "Esamina unioni di alias",
    "Running": "In corso",
    "Saved {count} new jobs; refreshed {duplicates}. Pages read: {pages}. {reason}": "Salvate {count} nuove offerte; aggiornate {duplicates}. Pagine lette: {pages}. {reason}",
    "Show resolved portals": "Mostra portali risolti",
    "Stop assisted ingestion": "Ferma acquisizione assistita",
    "Stopping assisted ingestion... Saved jobs are retained.": "Arresto acquisizione assistita... Le offerte salvate vengono conservate.",
    "Stopping background work. Saved batches are retained; close again when it finishes.": "Arresto delle attività in background. I lotti salvati vengono conservati; chiudi di nuovo al termine.",
    "Stopping scan. Close the application again after the scan has stopped.": "Arresto scansione. Chiudi nuovamente l'applicazione dopo il termine della scansione.",
    "These portals were blocked. They have been added to Assisted Ingestion. Use that tab to scan them when you are ready.": "Questi portali erano bloccati. Sono stati aggiunti ad Acquisizione Assistita. Usa quella scheda per scansionarli quando sei pronto.",
    "View Assisted Ingestion": "Vai ad Acquisizione Assistita",
    "Waiting for ingestion; {done} / {total} processed": "In attesa dell'acquisizione; elaborate {done} / {total}",
    "{count} queued portals": "{count} portali in coda",
    "{jobs} jobs / {pages} pages": "{jobs} offerte / {pages} pagine",
    "Database write failed: {error}. Saved jobs are retained.": "Scrittura nel database non riuscita: {error}. Le offerte salvate vengono conservate."
}
_FOLLOWUP_IT["These portals were blocked, but their queue could not be saved: {error}. Add the URLs from this run's artifacts manually in Assisted Ingestion."] = 'Questi portali erano bloccati, ma non è stato possibile salvare la coda: {error}. Aggiungi manualmente in Acquisizione Assistita gli URL presenti nei file di questa esecuzione.'
_FOLLOWUP_IT.update({'View JD access issue': 'Visualizza problema di accesso JD', 'JD access issue — listing jobs already saved. {reason}': 'Problema di accesso JD — annunci già salvati. {reason}', "Listing jobs are already saved. Some job-detail pages could not be read. This action does not start collection or bypass verification. See this run's scan log for the access evidence.": 'Gli annunci sono già salvati. Alcune descrizioni non erano leggibili. Questa azione non avvia la raccolta e non aggira le verifiche. Consulta il registro della scansione per le prove di accesso.', 'Listing or job-detail access issues have been added to Assisted Ingestion. Saved jobs are retained. JD-only entries show diagnostics and do not restart listing collection.': 'I problemi di accesso agli elenchi o alle descrizioni sono stati aggiunti ad Acquisizione Assistita. Gli annunci salvati sono conservati. Le voci solo JD mostrano la diagnostica e non riavviano la raccolta degli elenchi.', 'Nothing safe to resume. Runs without a saved target scope cannot be reconstructed safely; start an explicitly selected custom scan instead.': 'Nessuna scansione riprendibile in sicurezza. Le scansioni senza ambito salvato non possono essere ricostruite in sicurezza; avvia una scansione personalizzata con selezione esplicita.'})
_FOLLOWUP_IT.update({'Listing and JD access issues from your scans': 'Problemi di accesso a elenchi e descrizioni dalle tue scansioni', 'Listing and JD access issues': 'Problemi di accesso a elenchi e descrizioni', "These access issues could not be saved to the queue: {error}. Add the URLs from this run's artifacts manually in Assisted Ingestion.": 'Impossibile salvare questi problemi di accesso nella coda: {error}. Aggiungi manualmente in Acquisizione Assistita gli URL presenti nei file di questa esecuzione.'})
LANGUAGES['it'].update(_FOLLOWUP_IT)
LANGUAGES['en'].update({key:key for key in _FOLLOWUP_IT})


def _(text: str) -> str:
    """Translate a string to the current locale.

    Falls back to English (the key itself) if the string is not translated.
    """
    return LANGUAGES.get(_locale, {}).get(text, text)


def set_table_header_labels(table, labels):
    """Batch header notifications instead of triggering N auto-resizes.

    QTableWidget emits headerDataChanged per label; ResizeToContents tables
    can then remeasure all rows repeatedly on every locale switch. Notify
    once, and skip identical captions, preserving normal Qt resizing policy.
    """
    labels = list(labels)
    if all(table.horizontalHeaderItem(i) is not None and
           table.horizontalHeaderItem(i).text() == label
           for i, label in enumerate(labels)):
        return
    from PySide6.QtCore import QSignalBlocker, Qt
    model = table.model()
    with QSignalBlocker(model):
        table.setHorizontalHeaderLabels(labels)
    if labels:
        model.headerDataChanged.emit(Qt.Horizontal, 0, len(labels)-1)


def retranslate_widget_literals(root):
    """Update static labels without rebuilding a tab or touching input/worker state.

    Table data and editable text are not rewritten. Dynamic progress/results
    remain intact until the next progress event, already in the new locale.
    """
    from PySide6.QtWidgets import QWidget,QLabel,QAbstractButton,QGroupBox
    reverse={value:key for mapping in LANGUAGES.values() for key,value in mapping.items()}
    keys=set().union(*(mapping.keys() for mapping in LANGUAGES.values()))
    def translated(text):
        # The English mapping is intentionally sparse (keys are the default).
        key=text if text in keys else reverse.get(text)
        return _(key) if key is not None else text
    for widget in root.findChildren(QWidget):
        if isinstance(widget,QGroupBox):
            widget.setTitle(translated(widget.title()))
        elif isinstance(widget,(QLabel,QAbstractButton)):
            widget.setText(translated(widget.text()))
        if widget.toolTip():
            widget.setToolTip(translated(widget.toolTip()))
        # Placeholders are UI hints, not editable content (notably the four
        # AI Prompt Kit text editors). Preserve the user's actual text.
        placeholder = getattr(widget, 'placeholderText', None)
        setter = getattr(widget, 'setPlaceholderText', None)
        if callable(placeholder) and callable(setter):
            setter(translated(placeholder()))


# Approved shared geography/pagination and explicit data-review strings.
_COUNTRY_PAGING_IT = {'Observed {total} valid vacancies: {count} new, {duplicates} refreshed. Listing pages/batches: {pages}; browser read passes: {loads}. Country resolved: {resolved}; unresolved: {unknown}. {reason}': 'Osservate {total} offerte valide: {count} nuove, {duplicates} aggiornate. Pagine/lotti letti: {pages}; passaggi di lettura browser: {loads}. Paese risolto: {resolved}; non risolto: {unknown}. {reason}', 'Review data quality': 'Esamina qualità dati', 'Preview only until you confirm. Only unresolved observed countries and confirmed non-job actions are proposed. Manual countries and saved applications are protected. No rows or scan history are deleted.': 'Solo anteprima fino alla conferma. Proposte limitate a paesi osservati non risolti e azioni non lavorative confermate. Paesi manuali e candidature salvate sono protetti. Nessuna riga o cronologia viene eliminata.', 'Optional company filter (blank = all)': 'Filtro azienda facoltativo (vuoto = tutte)', 'Current Country': 'Paese attuale', 'Proposed Country': 'Paese proposto', 'Preview': 'Anteprima', 'Apply reviewed changes': 'Applica modifiche esaminate', 'Loading preview...': 'Caricamento anteprima...', 'Country fills: {countries}; non-job exclusions: {excluded}; protected: {protected}; still unresolved: {unknown}.': 'Paesi da completare: {countries}; azioni non lavorative da escludere: {excluded}; protetti: {protected}; ancora non risolti: {unknown}.', 'Apply exactly this preview? A full SQLite backup, SHA256 and audit are saved before changes. Non-job records are hidden from active search, not deleted. Historical counters and applications stay unchanged.': 'Applicare esattamente questa anteprima? Prima delle modifiche vengono salvati backup SQLite completo, SHA256 e audit. I record non lavorativi sono nascosti nella ricerca attiva, non eliminati. Contatori storici e candidature restano invariati.', 'Confirm data review': 'Conferma revisione dati', 'Saving backup and applying reviewed changes...': 'Salvataggio backup e applicazione delle modifiche esaminate...', 'Changed {count} records. Backup: {backup}\nAudit: {audit}': 'Modificati {count} record. Backup: {backup}\nAudit: {audit}', 'Review not applied: {error}': 'Revisione non applicata: {error}', 'Finish ingestion before reviewing existing data.': 'Termina l’acquisizione prima di esaminare i dati esistenti.', 'Data review is applying. Wait for the backup/audit result before closing.': 'Revisione dati in corso. Attendi il risultato di backup/audit prima di chiudere.', 'Resolving country evidence {done}/{total}; saved vacancies are retained.': 'Risoluzione evidenze del paese {done}/{total}; le offerte salvate restano conservate.', 'Fill missing country': 'Completa paese mancante', 'Exclude non-job action': 'Escludi azione non lavorativa', 'Protected': 'Protetto', 'Still unresolved': 'Ancora non risolto'}
for _key, _value in _COUNTRY_PAGING_IT.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value

LANGUAGES["en"].setdefault("Close", "Close")
LANGUAGES["it"]["Close"] = "Chiudi"
LANGUAGES["en"].setdefault("Reason", "Reason")
LANGUAGES["it"]["Reason"] = "Motivo"


# Human-cleared full-JD support integration, no classifier-rule retuning.
_SESSION_SUPPORT_IT = {'Reading full job descriptions {done}/{total}; saved vacancies are retained.': 'Lettura delle descrizioni complete {done}/{total}; le offerte salvate restano conservate.', 'JD text: {readable}/{total} readable; reused: {reused}; blocked: {blocked}; failed/thin/empty: {unreadable}; budget skipped: {skipped}. Readable without support evidence: {unknown}.': 'Testo JD: {readable}/{total} leggibili; riutilizzate: {reused}; bloccate: {blocked}; errori/brevi/vuote: {unreadable}; saltate per budget: {skipped}. Leggibili senza evidenza di supporto: {unknown}.', 'Action needed: job-detail verification at {url} — please solve the check in Chromium or stop.': 'Azione richiesta: verifica nei dettagli offerta {url} — risolvi il controllo in Chromium o interrompi.', 'Action needed: job-detail access was denied at {url}. Inspect Chromium or stop; no CAPTCHA was confirmed.': 'Azione richiesta: accesso negato ai dettagli {url}. Controlla Chromium o interrompi; nessun CAPTCHA confermato.'}
for _key, _value in _SESSION_SUPPORT_IT.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value


# Explicit Assisted transport and scoped unread-JD continuation.
_ASSISTED_RESUME_IT = {'Direct HTTP did not provide a JD; continuing in the existing Chromium detail tab within shared limits.': 'La richiesta HTTP non ha fornito una JD; continuo nella scheda dettagli Chromium esistente entro i limiti condivisi.', 'Resume unread JDs': 'Riprendi JD non lette', 'Continue the saved batch for this exact portal URL; keep fresh proven JDs and do not crawl listing pages again.': 'Continua il lotto salvato per questo URL portale esatto; conserva le JD recenti verificate senza rileggere le pagine elenco.', 'Resumed JD enrichment for {total} saved vacancies. No listing pages were crawled. {reason}': 'Arricchimento JD ripreso per {total} offerte salvate. Nessuna pagina elenco visitata. {reason}', 'Detail transport: HTTP requests {http}; HTTP access walls {walls}; browser attempts {browser}; preferred-browser reads {preferred}; browser retries {retries}.': 'Trasporto dettagli: richieste HTTP {http}; blocchi accesso HTTP {walls}; tentativi browser {browser}; letture browser preferite {preferred}; nuovi tentativi browser {retries}.', 'Resume checkpoint could not be saved: {error}': 'Impossibile salvare il punto di ripresa: {error}', 'Saved JD batch does not match this database and exact portal URL.': 'Il lotto JD salvato non corrisponde a questo database e URL portale esatto.', 'Saved JD batch is empty or invalid. Run Assisted listing ingestion first.': 'Il lotto JD salvato è vuoto o non valido. Esegui prima l’acquisizione assistita dell’elenco.', 'Saved JD batch contains an invalid posting identity.': 'Il lotto JD salvato contiene un’identità offerta non valida.', 'Saved JD batch contains duplicate posting URLs.': 'Il lotto JD salvato contiene URL offerta duplicati.', 'No saved JD batch for this exact portal URL. Run Assisted listing ingestion first.': 'Nessun lotto JD salvato per questo URL portale esatto. Esegui prima l’acquisizione assistita dell’elenco.', 'The saved run CSV is unavailable. Run Assisted listing ingestion first.': 'Il CSV dell’esecuzione salvata non è disponibile. Esegui prima l’acquisizione assistita dell’elenco.', 'This saved run has no auditable JD coverage. Run Assisted listing ingestion first.': 'L’esecuzione salvata non ha copertura JD verificabile. Esegui prima l’acquisizione assistita dell’elenco.'}
for _key,_value in _ASSISTED_RESUME_IT.items():
    LANGUAGES['en'].setdefault(_key,_key)
    LANGUAGES['it'][_key]=_value


# Current visible clearance and explicit recorded-state continuation.
_ASSISTED_VISIBLE_IT = {'Saved listing is already complete. Use Resume unread JDs.': 'L’elenco salvato è già completo. Usa Riprendi JD non lette.', 'No auditable observed listing continuation is saved. Start a fresh listing run; no page URL was guessed.': 'Non è salvato alcuno stato osservato verificabile per continuare l’elenco. Avvia una nuova acquisizione; nessun URL di pagina è stato ipotizzato.', 'Resume listing collection': 'Riprendi raccolta elenco', 'Continue only from an actually recorded listing URL; this is separate from unread JD continuation.': 'Continua solo da un URL elenco realmente registrato; questa azione è distinta dalla ripresa delle JD non lette.', 'Saved batch:': 'Lotto salvato:', 'Latest saved batch': 'Ultimo lotto salvato', 'Load saved batches': 'Carica lotti salvati', 'Verification is performed by you in Chromium; Continue only requests a fresh content check.': 'La verifica viene eseguita da te in Chromium; Continua richiede solo un nuovo controllo del contenuto.', 'Show verification browser': 'Mostra browser verifica', 'Continue after verification': 'Continua dopo verifica', 'Save partial and close': 'Salva parziale e chiudi', 'Listing complete': 'Elenco completo', 'Listing incomplete': 'Elenco incompleto', '{run}: {rows} saved jobs / {pages} pages — {state}': '{run}: {rows} offerte salvate / {pages} pagine — {state}', 'Could not load saved batches: {error}': 'Impossibile caricare i lotti salvati: {error}', 'Checking current visible content; a button click does not certify verification.': 'Controllo del contenuto attualmente visibile; un clic non certifica il superamento della verifica.', 'Saving partial results and closing the verification session...': 'Salvataggio dei risultati parziali e chiusura della sessione di verifica...', 'Awaiting your verification in Chromium. You may continue checking this page or save partial results.': 'In attesa della tua verifica in Chromium. Puoi ricontrollare questa pagina o salvare i risultati parziali.', 'Current content check finished; authentic primary content is still required.': 'Controllo del contenuto terminato; è comunque necessario il contenuto principale autentico.', 'Listing collection continued from recorded state; earlier pages were not restarted.': 'Raccolta elenco ripresa dallo stato registrato; le pagine precedenti non sono state riavviate.', 'Accepted clearance transitions: {cleared}; deferred by an access gate: {deferred}. A detector timeout is not a claim that you did not solve a check.': 'Transizioni di verifica accettate: {cleared}; rinviate per blocco di accesso: {deferred}. Un timeout del rilevatore non significa che tu non abbia risolto la verifica.'}
_ASSISTED_VISIBLE_IT['Listing continuation left the selected portal filters; out-of-scope rows were not accepted.']='La continuazione dell’elenco ha lasciato i filtri del portale selezionato; le righe fuori ambito non sono state accettate.'
for _key,_value in _ASSISTED_VISIBLE_IT.items():
    LANGUAGES['en'].setdefault(_key,_key)
    LANGUAGES['it'][_key]=_value


# Issue 2026-10-26: honest partial JD coverage and neutral idle controls.
_ASSISTED_ISSUE_2026_10_26_IT = {
    "JD text: {readable}/{total} readable; not attempted: {unattempted}; reused: {reused}; blocked: {blocked}; failed/thin/empty: {unreadable}; budget skipped: {skipped}; deferred/protected: {protected}. Readable JDs with Unknown support verdict: {unknown}.": "Testo JD: {readable}/{total} leggibili; non tentate: {unattempted}; riutilizzate: {reused}; bloccate: {blocked}; errori/brevi/vuote: {unreadable}; saltate per budget: {skipped}; rinviate/protette: {protected}. JD leggibili con verdetto di supporto sconosciuto: {unknown}.",
    "Verification controls are inactive.": "Controlli di verifica non attivi.",
    "Ingestion Partial": "Acquisizione parziale",
    "Some full job descriptions were not read. Use Resume unread JDs to continue; no Unknown support verdict has been converted to No.": "Alcune descrizioni complete non sono state lette. Usa Riprendi JD non lette per continuare; nessun verdetto di supporto sconosciuto è stato convertito in No.",
    "assisted browser closed during JD enrichment; {count} JDs unattempted; resume explicitly": "browser assistito chiuso durante l'arricchimento JD; {count} JD non tentate; riprendi esplicitamente",
}
for _key, _value in _ASSISTED_ISSUE_2026_10_26_IT.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value

# Phase B performance reporting (Issue 001, 2026-10-27).
_ASSISTED_PHASE_B_KEY = "Performance: up to {tabs} parallel detail tabs; {prefetched} JDs pre-navigated; HTTP re-probe cleared {cleared} origin(s)."
LANGUAGES["en"].setdefault(_ASSISTED_PHASE_B_KEY, _ASSISTED_PHASE_B_KEY)
LANGUAGES["it"][_ASSISTED_PHASE_B_KEY] = "Prestazioni: fino a {tabs} schede dettaglio parallele; {prefetched} JD pre-navigate; il re-probe HTTP ha ripristinato {cleared} origine/i."


# Issue 002 screenshot fixes (2026-10-27): honest dashboard labels/tooltips.
_ISSUE_002_DASHBOARD_KEYS = {
    "Top Employers by Sponsor Score": "Principali datori di lavoro per punteggio sponsor",
    "Max Sponsor Score": "Punteggio sponsor massimo",
    "Counts jobs with an explicit Yes visa-sponsorship verdict. Unknown is not No; unread JDs stay Unknown.": "Conta le offerte con verdetto esplicito Sì per la sponsorship del visto. Sconosciuto non significa No; le JD non lette restano Sconosciute.",
    "Counts jobs with an explicit Yes EU Blue Card verdict. Unknown is not No; unread JDs stay Unknown.": "Conta le offerte con verdetto esplicito Sì per la Carta Blu UE. Sconosciuto non significa No; le JD non lette restano Sconosciute.",
}
for _key, _value in _ISSUE_002_DASHBOARD_KEYS.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value


# Explicit portal-name/URL provenance confirmation (2026-11-01).
_PORTAL_IDENTITY_20261101 = {
    "Confirm portal identity": "Conferma identità del portale",
    "The URL differs from the selected queued portal, but the company name is unchanged. Continue with this exact name and URL?": "L'URL è diverso dal portale selezionato, ma il nome dell'azienda è invariato. Continuare con questo nome e URL esatti?",
}
for _key, _value in _PORTAL_IDENTITY_20261101.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value

# Browser-state clarity retained after removal of pasted-JD fallback.
_BROWSER_STATE_20261101 = {
    'View only: opening your browser does not start ingestion or share its session with the app.': 'Solo visualizzazione: aprire il browser non avvia l’acquisizione né condivide la sessione con l’app.',
    'Idle — no assisted browser is running. External browser is view-only.': 'Inattivo — nessun browser assistito in esecuzione. Il browser esterno serve solo alla visualizzazione.',
    'Verification controls are active only while an assisted session is waiting for your action. Idle is not waiting. Continue checks the app-controlled Chromium, not your external browser.': 'I controlli di verifica sono attivi solo quando una sessione assistita attende una tua azione. Inattivo non significa in attesa. Continua verifica il Chromium controllato dall’app, non il browser esterno.',
    'External browser opened for viewing only. No ingestion started and no browser session is shared. Launch Assisted starts a separate app-controlled browser; access is not guaranteed.': 'Browser esterno aperto solo per la visualizzazione. Nessuna acquisizione avviata e nessuna sessione condivisa. Avvia assistito apre un browser separato controllato dall’app; l’accesso non è garantito.',
}
for _key, _value in _BROWSER_STATE_20261101.items():
    LANGUAGES["en"].setdefault(_key, _key)
    LANGUAGES["it"][_key] = _value

# Approved final Assisted UI cleanup (no experimental browser pairing).
_ASSISTED_CLEAN_UI = {
    'Select a portal to collect jobs in the assisted browser. Access is not guaranteed.': 'Seleziona un portale per raccogliere offerte nel browser assistito. L’accesso non è garantito.',
    'Portal': 'Portale',
    'Start collection': 'Avvia raccolta',
    'Open the app-controlled browser and collect jobs from this portal.': 'Apri il browser controllato dall’app e raccogli le offerte da questo portale.',
    'Open website': 'Apri sito',
    'Idle — select a portal to begin.': 'Inattivo — seleziona un portale per iniziare.',
    'Complete verification in the assisted browser, then continue or save partial results.': 'Completa la verifica nel browser assistito, poi continua o salva i risultati parziali.',
    'Advanced options': 'Opzioni avanzate',
    'Saved batches, resume controls and run details.': 'Lotti salvati, controlli di ripresa e dettagli dell’esecuzione.',
    'Run details appear here.': 'I dettagli dell’esecuzione appariranno qui.',
    'Website opened for viewing only. Collection has not started.': 'Sito aperto solo per la visualizzazione. La raccolta non è stata avviata.',
    'Opening the assisted browser…': 'Apertura del browser assistito…',
    'Stopping collection… Saved jobs are retained.': 'Arresto della raccolta… Le offerte salvate vengono conservate.',
    'Collection finished. See Advanced options for details.': 'Raccolta terminata. Consulta le Opzioni avanzate per i dettagli.',
    'Collection failed. See Advanced options for details.': 'Raccolta non riuscita. Consulta le Opzioni avanzate per i dettagli.',
    'Partial result. See Advanced options for details.': 'Risultato parziale. Consulta le Opzioni avanzate per i dettagli.',
}
for _key, _value in _ASSISTED_CLEAN_UI.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Structured two-operation scanner progress. Counts are operational, not success.
_SCAN_WORK_PROGRESS = {
    'Portal tasks — waiting to start': 'Portali — in attesa di avvio',
    'JD checks — waiting for discovered candidates': 'Controlli descrizioni — in attesa dei candidati rilevati',
    'Checked does not mean readable. Totals grow as portals discover candidates.': 'Controllato non significa leggibile. I totali crescono quando i portali rilevano nuovi candidati.',
    'Active portal workers appear here.': 'Qui appariranno le attività dei portali in corso.',
    'Portals settled: {done}/{total} · pending retries: {retry}': 'Portali conclusi: {done}/{total} · nuovi tentativi in attesa: {retry}',
    'Listing/provider workers: {listing} · JD workers: {jd}': 'Attività elenco/provider: {listing} · attività descrizioni: {jd}',
    'JD candidates classified: {checked}/{observed} currently observed': 'Candidati descrizioni classificati: {checked}/{observed} attualmente rilevati',
    'Readable: {readable} · thin: {thin} · empty: {empty} · failed: {failed} · blocked: {blocked} · unattempted: {unattempted}': 'Leggibili: {readable} · brevi: {thin} · vuoti: {empty} · falliti: {failed} · bloccati: {blocked} · non tentati: {unattempted}',
    'Reading JDs': 'Lettura descrizioni',
    'Listing/provider collection (may include JD reads)': 'Raccolta elenco/provider (può includere letture descrizioni)',
    '{company} — {stage} · JD candidates: {checked}/{total}': '{company} — {stage} · candidati descrizioni: {checked}/{total}',
}
for _key, _value in _SCAN_WORK_PROGRESS.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value
for _key, _value in {
    'Portal tasks: %p%': 'Portali: %p%',
    'JD classifications: %p%': 'Classificazioni descrizioni: %p%',
}.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Pending access does not imply a human CAPTCHA or a permanent denial.
for _key, _value in {
    'Access unavailable on page %d; waiting for current page recovery. No CAPTCHA was confirmed.': 'Accesso non disponibile alla pagina %d; attesa del ripristino della pagina corrente. Nessun CAPTCHA confermato.',
    'Verification pending on page %d — waiting for the page to clear. Interact only if the page requests it.': 'Verifica in corso alla pagina %d — attesa del completamento. Interagisci solo se richiesto dalla pagina.',
    'Access or verification pending in Chromium. Allow automatic checks to finish; interact only if the page requests it. An Access Denied page may offer no check.': 'Accesso o verifica in corso in Chromium. Attendi il completamento dei controlli automatici; interagisci solo se richiesto dalla pagina. Una pagina di accesso negato potrebbe non offrire alcuna verifica.',
}.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Tools: readable section headings and compact performance control.
for _key, _value in {
    'Active portal workers': 'Portali attivi',
    'Live scan output': 'Registro scansione in tempo reale',
    'High-Performance Mode': 'Modalità ad alte prestazioni',
}.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Plain resume guidance retained after experimental browser modes were removed.
for _key, _value in {
    'Resume unread job descriptions from this saved batch.': 'Riprendi le descrizioni delle offerte non lette da questo lotto salvato.',
    'No saved jobs to resume. Load a non-empty saved batch, or use Start collection.': 'Nessuna offerta salvata da riprendere. Carica un lotto salvato non vuoto oppure usa Avvia raccolta.',
}.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Clear collection outcomes and manual-access guidance, not browser experiments.
for _key, _value in {
    'Collection stopped': 'Raccolta interrotta',
    'You stopped collection. Any jobs already saved are kept.': 'Hai interrotto la raccolta. Le offerte già salvate vengono conservate.',
    'Collection complete': 'Raccolta completata',
    'Collection finished. Saved jobs are available in the app.': 'Raccolta terminata. Le offerte salvate sono disponibili nell’app.',
    'Website access unavailable': 'Accesso al sito non disponibile',
    'The website blocked access or kept asking for verification, so SponsorScout could not finish reading it.\n\nUse Open website to view jobs and apply manually in your usual browser. If verification keeps looping, stop retrying.\n\nAny jobs already saved are kept. This does not mean the website has no vacancies.': 'Il sito ha bloccato l’accesso o ha continuato a richiedere la verifica, quindi SponsorScout non ha potuto completarne la lettura.\n\nUsa Apri sito web per consultare le offerte e candidarti manualmente nel tuo browser abituale. Se la verifica continua a ripetersi, interrompi i tentativi.\n\nLe offerte già salvate vengono conservate. Questo non significa che il sito non abbia offerte disponibili.',
    'Partial results saved': 'Risultati parziali salvati',
    'Collection stopped and any saved jobs are kept. You can view them in the app; use your usual browser for pages that remain inaccessible.': 'Raccolta interrotta; le offerte salvate vengono conservate e sono consultabili nell’app. Usa il tuo browser abituale per le pagine ancora inaccessibili.',
    'Collection incomplete': 'Raccolta incompleta',
    'Collection could not finish. Any jobs already saved are kept. See Advanced options for the reason and any available resume actions.': 'Non è stato possibile completare la raccolta. Le offerte già salvate vengono conservate. Consulta le Opzioni avanzate per il motivo e le eventuali azioni di ripresa.',
}.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value


# Final app-wide UI/popup audit: schema identifiers remain unchanged on disk.
_UI_AUDIT_IT = {
    "Scan log — {run_id}": "Registro scansione — {run_id}",
    "Scan result: {status}; saved={saved}, duplicates={duplicates}, quarantined={quarantined}":
        "Risultato scansione: {status}; salvati={saved}, duplicati={duplicates}, in quarantena={quarantined}",
    "Seed": "Origine", "Source": "Fonte", "Quar.": "Quar.",
    "Scope Rej.": "Fuori ambito", "Diagnostics": "Dettagli tecnici",
    "Duration (s)": "Durata (s)", "Seed URL": "URL origine",
    "Board Slug": "Identificativo portale", "English Friendly": "Compatibilità con l'inglese",
    "direct_employer": "datore di lavoro diretto", "recruiter": "agenzia di selezione",
    "government": "ente pubblico", "global": "globale", "seed_url": "URL di origine",
    "job_location": "sede di lavoro",
}
for _key, _value in _UI_AUDIT_IT.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value


# Explicit final JD coverage (a result, not a stuck/running progress bar).
_FINAL_COVERAGE_IT = {
    "Final JD coverage: %p%": "Copertura JD finale: %p%",
    "Final JD coverage: {checked}/{observed} candidates classified; {unattempted} not attempted. No JD workers are running.":
        "Copertura JD finale: {checked}/{observed} candidati classificati; {unattempted} non tentati. Nessun processo JD è in esecuzione.",
    "Final coverage is not a running progress indicator. Unattempted candidates remain unread; saved jobs are retained.":
        "La copertura finale non indica un'elaborazione in corso. I candidati non tentati restano non letti; le offerte salvate sono conservate.",
}
for _key, _value in _FINAL_COVERAGE_IT.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Unified explicit assisted action; phase outcomes stay distinct.
_ASSISTED_UNIFIED_IT = {
    'Collect / continue': 'Raccogli / continua',
    'verified': 'verificato', 'not verified': 'non verificato',
    'Collection failed. Saved jobs are retained.': 'Raccolta non riuscita. Le offerte salvate sono conservate.',
    'Listing: {listing}; descriptions: {readable}/{observed} readable. {queue_note}': 'Elenco: {listing}; descrizioni: {readable}/{observed} leggibili. {queue_note}',
    'Earlier saved-job gap not reconciled.': 'Lacuna delle offerte precedenti non riconciliata.',
    'Descriptions read; listing not verified': 'Descrizioni lette; elenco non verificato',
    'Listing collected; descriptions incomplete': 'Elenco raccolto; descrizioni incomplete',
    'All {total} collected descriptions are readable, but listing completeness is not verified. Saved jobs are available. Collect / continue checks the remaining listing work and reuses valid saved descriptions.': 'Tutte le {total} descrizioni raccolte sono leggibili, ma la completezza dell’elenco non è verificata. Le offerte salvate sono disponibili. Raccogli / continua verifica il lavoro rimanente e riutilizza le descrizioni salvate valide.',
    '{readable}/{total} descriptions are readable. Saved jobs are available. Collect / continue works on the saved descriptions without restarting the verified listing.': '{readable}/{total} descrizioni sono leggibili. Le offerte salvate sono disponibili. Raccogli / continua elabora le descrizioni salvate senza riavviare l’elenco verificato.',
    'Browser closed before access was confirmed': 'Browser chiuso prima della conferma di accesso',
    'The browser closed before a readable listing was accepted. This does not establish why it closed. Saved jobs are retained; website access is still unverified.': 'Il browser si è chiuso prima che venisse accettato un elenco leggibile. Questo non stabilisce il motivo della chiusura. Le offerte salvate sono conservate; l’accesso al sito non è ancora verificato.',
    'No saved batch; collecting this exact portal.': 'Nessun gruppo salvato; raccolta da questo preciso portale.',
    'Listing already verified; continuing saved descriptions only.': 'Elenco già verificato; continuano solo le descrizioni salvate.',
    'Continuing the recorded listing; valid saved descriptions will be reused.': 'Continuazione dell’elenco registrato; le descrizioni salvate valide saranno riutilizzate.',
    'Rechecking the incomplete listing; valid saved descriptions will be reused.': 'Nuova verifica dell’elenco incompleto; le descrizioni salvate valide saranno riutilizzate.',
}
for _key, _value in _ASSISTED_UNIFIED_IT.items():
    LANGUAGES['en'].setdefault(_key, _key)
    LANGUAGES['it'][_key] = _value

# Approved access/outcome UI: records, classifications and readability are distinct.
_ACCESS_OUTCOME_UI_IT = {
    'Saved Jobs': 'Offerte salvate',
    'Saved active job records, not verified full descriptions or sponsorship. See the Scanner JD outcomes for readable, blocked and unattempted descriptions.': 'Offerte attive salvate, non descrizioni complete o sponsorizzazioni verificate. Consulta gli esiti JD dello scanner per descrizioni leggibili, bloccate e non tentate.',
    'Final JD outcomes classified: %p%': 'Esiti JD finali classificati: %p%',
    'Final JD outcomes: {checked}/{observed} classified; {unattempted} not attempted. Readable full descriptions: {readable}/{observed} ({readable_percent}%). No JD workers are running.': 'Esiti JD finali: {checked}/{observed} classificati; {unattempted} non tentati. Descrizioni complete leggibili: {readable}/{observed} ({readable_percent}%). Nessun processo JD in esecuzione.',
    'Classification includes readable, thin, empty, failed and blocked outcomes. It is not the percentage of readable descriptions. Unattempted is not proof of blocked access. Saved jobs are retained.': 'La classificazione comprende esiti leggibili, brevi, vuoti, falliti e bloccati. Non indica la percentuale di descrizioni leggibili. Non tentato non prova un blocco di accesso. Le offerte salvate sono conservate.',
    'Select a queue row to read the full access reason and latest result.': 'Seleziona una riga per leggere il motivo completo e l’ultimo risultato.',
    'Job descriptions: access unresolved. Saved records do not prove listing completeness. {reason}': 'Descrizioni: accesso non risolto. Le offerte salvate non provano la completezza dell’elenco. {reason}',
    'Listing access unresolved. This does not mean there are no vacancies. {reason}': 'Accesso all’elenco non risolto. Non significa che non ci siano offerte. {reason}',
}
for _key,_value in _ACCESS_OUTCOME_UI_IT.items():
    LANGUAGES['en'].setdefault(_key,_key)
    LANGUAGES['it'][_key]=_value

for _key,_value in {
    'Portal tasks finished (includes partial/errors): %p%': 'Attività portali terminate (incluse parziali/errori): %p%',
    'Listing or job-detail access issues have been added to Assisted Ingestion. Saved jobs are retained. Some descriptions may be unattempted after host protection paused reads. Collect / continue reuses saved work; only verified listings skip listing collection.': 'I problemi di accesso sono stati aggiunti ad Acquisizione Assistita. Le offerte salvate sono conservate. Alcune descrizioni potrebbero non essere state tentate dopo la sospensione protettiva delle letture. Raccogli / continua riutilizza il lavoro salvato; solo gli elenchi verificati non vengono raccolti nuovamente.',
}.items():
    LANGUAGES['en'].setdefault(_key,_key)
    LANGUAGES['it'][_key]=_value


# Approved Insights scopes, three-way skills and simple everyday workflows.
_INSIGHTS_SIMPLE_IT = {
    "Chart total": "Totale grafico",
    "In-Demand Soft Skills": "Competenze trasversali richieste",
    "Professional & Domain Skills": "Competenze professionali e di settore",
    "Selected jobs: %d | Categorised: %d / %d (%.1f%%) | With Skills: %d (%.1f%%)": "Offerte selezionate: %d | Classificate: %d / %d (%.1f%%) | Con competenze: %d (%.1f%%)",
    "Employer industry describes the company, not the job function: a healthcare company can hire salespeople or engineers. Chart total is the full distribution in that doughnut; a highlighted slice is the selection. Skills and coverage use the selected jobs.": "Il settore del datore di lavoro descrive l’azienda, non la mansione: un’azienda sanitaria può assumere commerciali o ingegneri. Il totale del grafico comprende l’intera distribuzione rappresentata; lo spicchio evidenziato indica la selezione. Competenze e copertura si riferiscono alle offerte selezionate.",
    "Advanced maintenance and diagnostics": "Manutenzione e diagnostica avanzate",
    "When a normal scan leaves an access issue, use this tab. 1. Select a company in the queue below, or enter its name and careers URL. 2. Click Collect / continue and follow any verification prompt in the app browser. 3. Wait for the saved-job result; use Stop if access keeps failing. Open website is view-only. Advanced contains saved batches and separate listing/JD resume controls.": "Usa questa scheda se una scansione normale lascia problemi di accesso. 1. Seleziona un’azienda nella coda qui sotto, oppure inserisci il nome e l’URL della pagina carriere. 2. Fai clic su Raccogli / continua e segui le eventuali richieste di verifica nel browser dell’app. 3. Attendi il risultato delle offerte salvate; usa Ferma se l’accesso continua a fallire. Apri sito web serve solo per consultare il sito. Le opzioni avanzate includono i lotti salvati e comandi distinti per riprendere l’elenco o le descrizioni delle offerte.",
}
for _key,_value in _INSIGHTS_SIMPLE_IT.items():
    LANGUAGES['en'].setdefault(_key,_key)
    LANGUAGES['it'][_key]=_value

# Plain Italian terminology: seed = configured company, job = vacancy.
_ITALIAN_TERMINOLOGY_REVIEW = {
    'Open website': 'Apri sito web',
    'Scanner description': 'Usa Scansiona ora per cercare offerte nelle aziende configurate. Usa Scansione personalizzata per sceglierne alcune. Pausa sospende la scansione; Ferma salva un punto di ripresa. Lo stato e i risultati compaiono qui sotto.',
    'Scan every seeded company (ATS boards + career pages) and enrich each job from its detail page, so no listing misses its evidence.': 'Cerca offerte in tutte le aziende configurate, nei portali ATS e nelle pagine carriere, e prova a leggerne le descrizioni complete. Le offerte salvate restano distinte dalle descrizioni effettivamente lette.',
    'Choose specific companies and/or source types (ATS and/or career portals) to scan instead of every seeded company.': 'Scegli le aziende e i tipi di fonte da analizzare: portali ATS, pagine carriere o entrambi, anziché tutte le aziende configurate.',
    'Choose the source types and companies to scan. Selected companies are scanned exactly like in a full scan — the full scan simply covers every seeded company.': 'Scegli le fonti e le aziende da analizzare. Le aziende selezionate vengono elaborate come nella scansione completa, che include tutte le aziende configurate.',
    'Freshness Check description': 'Ricontrolla le pagine delle offerte salvate e contrassegna quelle scadute.',
}
LANGUAGES['it'].update(_ITALIAN_TERMINOLOGY_REVIEW)

# Repeat-scan policy: all new visible text has an explicit Italian translation.
_INCREMENTAL_REFRESH_IT = {'Read saved descriptions again (next new run only)': 'Rileggi le descrizioni salvate (solo prossima nuova esecuzione)', 'Usually leave this off. Repeat scans revisit listings and reuse confirmed descriptions from the last 24 hours unless observed job details changed. This option requests new detail reads within the selected scope; access checks and limits still apply. Resume keeps the original campaign policy.': 'Normalmente lascia questa opzione disattivata. Le scansioni successive ricontrollano gli elenchi e riutilizzano le descrizioni confermate nelle ultime 24 ore, salvo modifiche osservate nell’offerta. Questa opzione richiede nuove letture per le aziende selezionate; restano validi controlli di accesso e limiti. Riprendi mantiene la regola della scansione originale.', 'Read saved descriptions again for this new run? This can take longer. Saved text is retained if a fresh read fails. Availability checks are separate.': 'Rileggere le descrizioni salvate per questa nuova esecuzione? Potrebbe richiedere più tempo. Se una nuova lettura non riesce, il testo salvato viene conservato. I controlli di disponibilità sono separati.', 'Read descriptions again?': 'Rileggere le descrizioni?', 'Counts cover observed jobs, not all jobs on inaccessible listings. Blocked and inconclusive are subsets of unread/unresolved; unattempted jobs are not called blocked.': 'I conteggi riguardano le offerte osservate, non tutte quelle di elenchi inaccessibili. Bloccate e inconcludenti sono comprese tra le non lette/irrisolte; le offerte non tentate non vengono indicate come bloccate.', 'Observed jobs — new: {new}; already saved: {existing}.': 'Offerte osservate — nuove: {new}; già salvate: {existing}.', 'Descriptions — reused: {reused}; read afresh: {fresh}; unread/unresolved: {pending} (blocked: {blocked}; inconclusive: {inconclusive}).': 'Descrizioni — riutilizzate: {reused}; lette nuovamente: {fresh}; non lette/irrisolte: {pending} (bloccate: {blocked}; inconcludenti: {inconclusive}).', 'Counts are partial: some observations could not be tracked.': 'Conteggi parziali: non è stato possibile registrare alcune osservazioni.', 'Job availability': 'Disponibilità delle offerte'}
for _key, _value in _INCREMENTAL_REFRESH_IT.items():
    LANGUAGES['en'][_key] = _key
    LANGUAGES['it'][_key] = _value

LANGUAGES['en']['Scanner description'] = 'Scan now revisits configured company listings to find new jobs. Recent, confirmed descriptions can be reused; new, changed, stale or unread jobs need detail reads. Use Custom Scan for fewer companies. Pause waits; Stop saves a checkpoint.'
LANGUAGES['it']['Scanner description'] = 'Scansiona ora ricontrolla gli elenchi delle aziende configurate per trovare nuove offerte. Le descrizioni recenti e confermate possono essere riutilizzate; le offerte nuove, modificate, datate o non lette richiedono la lettura dei dettagli. Usa Scansione personalizzata per scegliere meno aziende. Pausa sospende; Ferma salva un punto di ripresa.'
LANGUAGES['en']['Freshness Check description'] = 'Check whether saved jobs are still available. This does not force descriptions to be read again. Jobs checked in the last 7 days are skipped.'
LANGUAGES['it']['Freshness Check description'] = 'Controlla se le offerte salvate sono ancora disponibili. Non forza la rilettura delle descrizioni. Le offerte già controllate negli ultimi 7 giorni vengono saltate.'

# Finished JD coverage is a static result, not unfinished work progress.
_FINISHED_JD_RESULTS_IT = {
    'Final description results: {readable}/{observed} readable ({readable_percent}%); {unresolved} unresolved. No description workers are running.': 'Risultati finali delle descrizioni: {readable}/{observed} leggibili ({readable_percent}%); {unresolved} irrisolte. Nessuna lettura delle descrizioni è in corso.',
    'No job descriptions were observed in this run. No description workers are running.': 'Nessuna descrizione di offerta osservata in questa esecuzione. Nessuna lettura delle descrizioni è in corso.',
    'Description results are unavailable for this run. No description workers are running.': 'Risultati delle descrizioni non disponibili per questa esecuzione. Nessuna lettura delle descrizioni è in corso.',
}
for _key, _value in _FINISHED_JD_RESULTS_IT.items():
    LANGUAGES['en'][_key] = _key
    LANGUAGES['it'][_key] = _value

# Installed browser reuse and explicit app-private full Chromium fallback.
_BROWSER_SETUP_IT = {
    'Browser setup': 'Configurazione browser',
    'Choose an installed Chromium-family browser. Validation briefly opens an isolated window and also checks headless mode. Personal profiles are never used. Firefox is available for manual browsing only.': 'Scegli un browser installato della famiglia Chromium. La verifica apre brevemente una finestra isolata e controlla anche la modalità senza finestra. I profili personali non vengono mai usati. Firefox resta disponibile solo per la navigazione manuale.',
    'No compatible browser? Download one full Chromium into SponsorScout storage? This uses network and disk space. No separate headless shell is installed. The download is removed with SponsorScout data.': 'Nessun browser compatibile? Scaricare un solo Chromium completo nello spazio di SponsorScout? Sono necessari rete e spazio su disco. Non viene installata una shell headless separata. Il download viene rimosso insieme ai dati di SponsorScout.',
    'Detect browsers': 'Rileva browser',
    'Validate selected browser': 'Verifica browser selezionato',
    'Download app-managed Chromium': 'Scarica Chromium gestito dall’app',
    'Select a browser, then validate both modes.': 'Seleziona un browser, poi verifica entrambe le modalità.',
    'Save browser': 'Salva browser',
    'No supported browser detected. Install a Chromium-family browser or explicitly download Chromium.': 'Nessun browser supportato rilevato. Installa un browser della famiglia Chromium oppure scegli esplicitamente di scaricare Chromium.',
    'Working... Browser validation/download runs in the background.': 'Operazione in corso... La verifica o il download del browser viene eseguito in background.',
    'Both browser modes passed. Click Save browser to use this selection.': 'Entrambe le modalità hanno superato la verifica. Premi Salva browser per usare questa scelta.',
    'Browser setup failed: %s': 'Configurazione browser non riuscita: %s',
    'Download Chromium?': 'Scaricare Chromium?',
    'Finish or stop the active operation before changing browsers.': 'Completa o interrompi l’operazione attiva prima di cambiare browser.',
}
LANGUAGES['it'].update(_BROWSER_SETUP_IT)
LANGUAGES['en'].update({key:key for key in _BROWSER_SETUP_IT})
