"""Script page texts and helpers (toolkit-free)."""

EXAMPLE = '''# Fill in a form: copy, switch window, paste, confirm
key ctrl+c
wait 200
key alt+tab
wait 400
key ctrl+v
if window "Sign in"
  key enter
end
'''

HELP = ("Commands: key ctrl+shift+t | text Hello {date} | wait 200 | click left|right|middle|double | scroll -3 | media mute | open https://... | app calc | file path | "
        "notify text | shell cmd | run other_script | set name = value | stop\n"
        "Blocks: repeat 3 ... end    if [not] window \"text\" / process \"text\" / clipboard \"text\" / time 09:00-17:00 / weekday mon-fri ... [else ...] end\n"
        "{date} {time} {clipboard} {counter:n} {uuid} {random:1-6} and your own {name} variables work in text and arguments.")


def count_commands(nodes):
    """Commands written in the script (loop bodies counted once)."""
    n = 0
    for it in nodes:
        n += 1 if it["t"] == "cmd" else count_commands(it.get("body", [])) + count_commands(it.get("then", [])) + count_commands(it.get("else", []))
    return n
