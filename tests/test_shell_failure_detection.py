"""Shell output failure detection for bash tool."""

from nls.platform_shell import looks_like_shell_command_failure


def test_wsl_bash_syntax_error_is_failure():
    out = (
        "bash: -c: line 1: syntax error near unexpected token `;'\n"
        "bash: -c: line 1: `apt-get update -qq 2>  ; apt-get install -y -qq jq'"
    )
    assert looks_like_shell_command_failure(out, "wsl bash -c ...")


def test_cmdlet_not_found_is_failure():
    out = "curl.exe.exe : The term 'curl.exe.exe' is not recognized as the name of a cmdlet"
    assert looks_like_shell_command_failure(out, "curl.exe.exe -s ...")


def test_powershell_parser_error_is_failure():
    out = (
        "At C:\\Users\\umber\\AppData\\Roaming\\babo-desktop\\data\\skills\\discord-admin\\setup-babo-server.ps1:13 char:19\r\n"
        "+     @{ name = 'dY'? Community'; type = 4 },\r\n"
        "+                   ~\r\n"
        "Unexpected token '?' in expression or statement."
    )
    assert looks_like_shell_command_failure(out, "powershell -NoProfile -File setup.ps1")


def test_powershell_null_array_is_failure():
    out = "Cannot index into a null array.\r\nAt line:1 char:772\r\n+ ..."
    assert looks_like_shell_command_failure(out, "powershell ...")
