# Demo video — 3 minutes (script)

No command is typed in the video: everything happens in the panel. Record at 1920x1080, light
theme, Edith restarted from the desktop shortcut, old recipes deleted (Options -> Recipes).

Prepare: `python demo/make_letters.py` (opens nothing; writes `demo/data/letters/`), open
`invoices.xlsx` in Excel and `Payment reminder.docx` in Word side by side. For part 4, start
Northwind Desktop once by hand: `powershell -File demo/desktop/northwind.ps1 -Store demo/data/northwind.jsonl`.

| Time | On screen | Voice (say it in your own words) |
|---|---|---|
| 0:00-0:15 | Excel with 30 invoices, an empty Word template | "Every week I write the same letter for a different client. Teaching a computer to do it still takes a programmer. What if I could just show it once?" |
| 0:15-0:45 | Type ONE letter by hand from row 1 (client name in bold), Save As "Payment reminder - Paper Moth Bakery.docx" | "So I do it once, by hand, like always." |
| 0:45-1:05 | Click the dot -> "Edith, learn this". The gray line: "Muse Spark is compiling it (level 1)… Checking it against your document…" -> "Learned: Payment reminder" | "Edith found what I just did, and Muse Spark turned it into a small program — at the lowest level that gives exactly my result — and checked it against my own letter." |
| 1:05-1:30 | "do the same for Panadería Ñandú" -> preview in half a second, changes in blue, small gray "Compiled by Muse Spark · Level 1 · Done in 0.5 s" -> Enter -> the letter appears in the folder; open it | "Next time it takes half a second, with accents and all. Nothing is saved until I press Enter, and Undo is always there." |
| 1:30-1:55 | "for all of them" -> 30 letters in ~3 s -> Enter. Then "do the same for Lazy Comet" -> "Which one: Lazy Comet Coffee Bar, Lazy Comet Print House?" | "All thirty at once. And when it's not sure, it asks instead of guessing." |
| 1:55-2:20 | Preview for another client -> Correct -> change "Best regards," to "Kind regards," -> Enter -> gray "Learned: …" -> next client already says "Kind regards," | "If I correct it once, it remembers. That's a lesson — it applies by itself from now on." |
| 2:20-2:40 | "registra en Northwind la factura de Taller Ñuñoa" (an app with no API at all) -> preview of the fields -> Enter -> the record is in Northwind -> gray "Learned this task (level 4)" | "Even an app with only a user interface: Edith works on a hidden desktop — no mouse, nothing on my screen — and learns it as a recipe too." |
| 2:40-3:00 | Ask "what did the teacher say about the exam?" -> answer; then the README results table | "It also remembers the last minutes of my screen. My screen never leaves my PC; only what I ask goes to the cloud. Show it once. Edith does the next ones." |

Fallbacks: if "learn this" says it didn't see the template, open the template first, then Save
As a new name (Edith uses Windows' recent files). If a step is slow on camera, cut it in editing —
the README has the real timings.
