"""Everything the inventor says: short, warm, a little funny. English (video) and neutral Spanish."""

from __future__ import annotations

import random

PHRASES = {
    "en": {
        "hello": ["Hi! I'll be around.", "Workshop's open."],
        "workshop": ["Off to my workshop.", "I'll be at the bench."],
        "back": ["Back!", "Did you miss me?"],
        "catch": ["Got them!", "Nice throw!"],
        "rewind": ["Let me rewind the film...", "Rewinding..."],
        "found_demo": ["You did this one at {time}. I'll build a machine.",
                       "I saw you do it at {time}. Building!"],
        "found_machine": ["I already have a machine for this: {name}.", "{name} knows this one."],
        "building": ["Hammering blocks...", "One block per step."],
        "verifying": ["Checking it against what you did...", "Stamping..."],
        "verified": ["Same as yours. Every field.", "Matches your demo."],
        "repairing": ["One block is sparking. Fixing just that one.", "Oops. Replacing one block."],
        "show_me_one": ["Show me one, and I'll do the rest!", "I haven't seen this yet. Show me one?"],
        "fan": ["Here's what I'll send. Enter to go, Esc to cancel.",],
        "cancelled": ["Okay, cards back to you.", "No problem, nothing sent."],
        "done": ["Done! {ok} sent{skipped}. That's {saved} back to you.", "All {ok} in{skipped}. {saved} saved!"],
        "skipped": [", {n} skipped (empty cells)"],
        "failed": ["Something broke. I stopped before sending more.", "Sparks! I stopped to be safe."],
        "repeat": ["Want me to do it?"],
        "repeat_no": ["Okay, I won't ask about this again."],
        "proof_failed": ["My proof failed, so I won't run it like this.",],
    },
    "es": {
        "hello": ["¡Hola! Ando por aquí.", "El taller está abierto."],
        "workshop": ["Me voy a mi taller.", "Estaré en el banco de trabajo."],
        "back": ["¡Volví!", "¿Me extrañaste?"],
        "catch": ["¡Las tengo!", "¡Buen lanzamiento!"],
        "rewind": ["Déjame rebobinar la película...", "Rebobinando..."],
        "found_demo": ["Esto lo hiciste a las {time}. Armo una máquina.",
                       "Te vi hacerlo a las {time}. ¡A construir!"],
        "found_machine": ["Ya tengo una máquina para esto: {name}.", "{name} ya sabe hacerlo."],
        "building": ["Martillando bloques...", "Un bloque por paso."],
        "verifying": ["Comparándola con lo que hiciste...", "Sellando..."],
        "verified": ["Igual que la tuya. Campo por campo.", "Coincide con tu demostración."],
        "repairing": ["Un bloque echa chispas. Arreglo solo ese.", "Ups. Cambio un bloque."],
        "show_me_one": ["Muéstrame una y yo hago el resto.", "Esto no lo he visto. ¿Me muestras una?"],
        "fan": ["Esto es lo que voy a enviar. Enter para seguir, Esc para cancelar."],
        "cancelled": ["Listo, te devuelvo las tarjetas.", "Sin problema, no envié nada."],
        "done": ["¡Listo! {ok} enviadas{skipped}. Te devolví {saved}.", "¡{ok} adentro{skipped}! {saved} ahorrados."],
        "skipped": [", {n} omitidas (celdas vacías)"],
        "failed": ["Algo se rompió. Me detuve antes de enviar más.", "¡Chispas! Me detuve por seguridad."],
        "repeat": ["¿Quieres que lo haga yo?"],
        "repeat_no": ["Está bien, no te pregunto más por esto."],
        "proof_failed": ["Mi prueba falló, así que no la uso así."],
    },
}


def say(key: str, lang: str = "en", **kw) -> str:
    options = PHRASES.get(lang, PHRASES["en"]).get(key) or PHRASES["en"].get(key) or [key]
    return random.choice(options).format(**kw)


def duration(seconds: float, lang: str = "en") -> str:
    minutes = round(seconds / 60)
    if minutes >= 1:
        return f"{minutes} min"
    return f"{round(seconds)} s"
