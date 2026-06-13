# Hero GIF: index your history, then recall a fact (with its conflict).
# Render from repo root:
#   ROD_BROWSER_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" vhs docs/demo/hero.tape
Output docs/img/hero.gif

Set Shell bash
Set FontSize 26
Set Width 1280
Set Height 760
Set Theme { "background": "#0a0e0a", "foreground": "#5cff8f", "cursor": "#5cff8f", "selection": "#1d3a26", "black": "#0a0e0a", "red": "#ff6b6b", "green": "#5cff8f", "yellow": "#e9ff6b", "blue": "#5cd0ff", "magenta": "#c98bff", "cyan": "#5cffe0", "white": "#c8ffd4", "brightBlack": "#46584d", "brightRed": "#ff8b8b", "brightGreen": "#8effb0", "brightYellow": "#f2ff8b", "brightBlue": "#8be0ff", "brightMagenta": "#dab0ff", "brightCyan": "#8bfff0", "brightWhite": "#eaffee" }
Set Padding 28
Set BorderRadius 10
Set Margin 16
Set TypingSpeed 55ms

Hide
Type "export PS1='❯ '"
Enter
Type 'cd ~/Documents/Github/memory-agent/docs/demo && export PATH="$PWD:$PATH"'
Enter
Type "clear"
Enter
Show

Sleep 600ms
Type "memory index"
Sleep 300ms
Enter
Sleep 5s
Type "clear"
Enter
Sleep 400ms
Type `memory recall "my cache setup"`
Sleep 300ms
Enter
Sleep 5s
