# Viewer GIF: the real `mem` viewer over a live knowledge base.
#   mem view -> find -> read a note -> why (receipts) -> conflicts (supersede log)
#
# NOTE: as written this records YOUR live personal KB (MEMORY_AGENT_DATA below) — review the gif
# before publishing, or delete that export line to record the repo's local knowledge/ instead.
#
# Render from repo root:
#   ROD_BROWSER_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" vhs docs/demo/view.tape
Output docs/img/view.gif

Set Shell bash
Set FontSize 20
Set Width 1400
Set Height 900
Set Theme { "background": "#16161e", "foreground": "#c0caf5", "cursor": "#7aa2f7", "selection": "#283457", "black": "#15161e", "red": "#f7768e", "green": "#9ece6a", "yellow": "#e0af68", "blue": "#7aa2f7", "magenta": "#bb9af7", "cyan": "#7dcfff", "white": "#a9b1d6", "brightBlack": "#414868", "brightRed": "#f7768e", "brightGreen": "#9ece6a", "brightYellow": "#e0af68", "brightBlue": "#7aa2f7", "brightMagenta": "#bb9af7", "brightCyan": "#7dcfff", "brightWhite": "#c0caf5" }
Set Padding 26
Set BorderRadius 10
Set Margin 16
Set TypingSpeed 45ms

Hide
Type "export PS1='❯ '"
Enter
Type 'cd ~/Documents/Github/memory-agent && export MEMORY_AGENT_DATA="$HOME/.local/share/memory-agent"'
Enter
Type "alias mem='python3 mem.py'"
Enter
Type "clear"
Enter
Show

Sleep 600ms
Type "mem view"
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Enter
Type `mem find "voice clone"`
Sleep 400ms
Enter
Sleep 4s

Type "clear"
Enter
Type "mem view voice-cloning-stack"
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Enter
Type "mem why anthropic-sdk-usage --limit 3"
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Enter
Type "mem conflicts --limit 4"
Sleep 400ms
Enter
Sleep 6s
