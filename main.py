import json
import socket
import sys
import uuid
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Log,
)

import e2ee

base_dir = Path(__file__).resolve().parent
_CHAT_PREFIX = "chat-"
data_path   = base_dir / "data"
styles_path = base_dir / "data"
chats_path  = data_path / "chats"
lang_path   = data_path / "lang.json"
config_path = data_path / "user-config.json"
current_chat_folder: Path | None = None

chat_name_selected = "No chat selected"

class CreateChatScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Enter chat name", id="rename-input")

class RenameChatScreen(ModalScreen[str | None]):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Enter new chat name, type nothing and press Enter to cancel", id="rename-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.input.value.strip() or None)

class MessengerApp(App):
    CSS_PATH = styles_path / "style.tcss"

    BINDINGS = [
        Binding("n", "new_chat", "New Chat", show=True),
        Binding("r", "rename_chat", "Rename Chat", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield ListView(id="chat-list")

            with Vertical(id="chat-area"):
                yield Log(id="output", auto_scroll=True, highlight=True)
                yield Input(placeholder="Press / to type", id="inputbar")

        yield Footer()

    def on_mount(self) -> None:
        with open(config_path, "r", encoding="utf-8") as f:
            self.my_config = json.load(f)

        self.known_chats: dict[str, Path] = {}
        self.chat_names:  dict[str, str]  = {}
        self._last_mtime: float = 0.0

        self.load_chats()
        self.set_interval(0.5, self.load_chats)
        self.set_interval(0.5, self.refresh_history)

        self.title = chat_name_selected
        self.sub_title = " @interfector4ngelorum"

    def load_chats(self) -> None:
        # Reads chats/ and adds only new chats to ListView
        chat_list = self.query_one("#chat-list", ListView)

        if not chats_path.exists():
            chats_path.mkdir(parents=True, exist_ok=True)
 
        seen_ids = set()
        for folder in sorted(Path(chats_path).iterdir()):
            if not folder.is_dir():
                continue

            config_file_local = folder / "config.json"
            if not config_file_local.is_file():
                continue

            try:
                with config_file_local.open(encoding="utf-8") as f:
                    chat_config = json.load(f)
            except Exception:
                chat_config = {}

            chat_name = chat_config.get("name") or folder.name
            chat_id = folder.name
            seen_ids.add(chat_id)

            if chat_id in self.known_chats:
                continue

            self.known_chats[chat_id] = folder
            self.chat_names[chat_id]  = chat_name
            item = ListItem(Label(chat_name), id=_widget_id(chat_id))
            chat_list.append(item)
        # Remove non-existing chats from the list
        for chat_id in list(self.known_chats):
            if chat_id not in seen_ids:
                del self.known_chats[chat_id]
                self.chat_names.pop(chat_id, None)
                for item in chat_list.children:
                    if item.id == _widget_id(chat_id):
                        item.remove()
                        break

    def refresh_history(self) -> None:
        if current_chat_folder is None:
            return
        history = current_chat_folder / "history.txt"

        log = self.query_one("#output", Log)
        if not history.is_file():
            log.clear()
            return

        mtime = history.stat().st_mtime
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime

        log.clear()
        with history.open(encoding="utf-8") as f:
            for line in f:
                log.write_line(line.strip("\n"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        global current_chat_folder
        widget_id = event.item.id
        if widget_id is None or not widget_id.startswith(_CHAT_PREFIX):
            return
        chat_id = widget_id[len(_CHAT_PREFIX):]
            
        folder = self.known_chats.get(chat_id)
        if folder is None:
            return

        current_chat_folder = folder
        self.title = self.chat_names[chat_id]
        self._last_mtime = 0.0
        self.refresh_history()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        output = self.query_one("#output", Log)
        if event.input.value == "":
            return
        if current_chat_folder is None:
            self.notify("No chat selected.")
            return

        user_name = self.my_config.get("name", "user")
        safe_name = escape(user_name)
        safe_text = escape(event.value)

        output.write_line(f"{safe_name}: {safe_text}")

        with (current_chat_folder / "history.txt").open("a", encoding="utf-8") as f:
            f.write(f"{user_name}: {event.value}\n")

        event.input.value = ""

    def action_new_chat(self) -> None:
        new_id = str(uuid.uuid4())
        folder = Path(chats_path) / new_id
        folder.mkdir(parents=True, exist_ok=True)

        with (folder / "config.json").open("w", encoding="utf-8") as f:
            json.dump({"name": f"Chat {new_id[:8]}"}, f, ensure_ascii=False, indent=4)

        (folder / "history.txt").touch()

        self.load_chats()
        self.notify(f"Created Chat: {new_id[:8]}")

    def action_rename_chat(self) -> None:
        if current_chat_folder is None:
            self.notify("No chat selected.")
            return

        self.push_screen(RenameChatScreen(), callback=self.on_rename_done)

    def on_rename_done(self, new_name: str | None ) -> None:
        if not new_name or current_chat_folder is None:
            return
        chat_id = current_chat_folder.name
        
        try:
            config_file = current_chat_folder / "config.json"
            with config_file.open("r", encoding="utf-8") as f:
                config = json.load(f)
            
            config["name"] = new_name
            
            with config_file.open("w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return

        self.chat_names[chat_id] = new_name
        try:
            item = self.query_one(f"#{_widget_id(chat_id)}", ListItem)
            item.query_one(Label).update(new_name)
        except Exception:
            pass
        self.title = new_name
        self.notify(f"Renamed to: {new_name}")
        
def get_my_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def find_port(start_port):
    port = start_port
    while True:
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            test_socket.bind(('0.0.0.0', port))
            return test_socket, port
        except OSError:
            test_socket.close()
            port += 1

def ensure_files() -> None:
    if not config_path.is_file():
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            file = json.load(f)
    except Exception:
        sys.exit(1)

    if not file.get("id"):
        file["id"] = str(uuid.uuid4())
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file, f, ensure_ascii=False, indent=4)

def _widget_id(chat_id: str) -> str:
    return _CHAT_PREFIX + chat_id

if __name__ == "__main__":
    ensure_files()
    with open(config_path, "r", encoding="utf-8") as f:
                file_main = json.load(f)
    working_socket, actual_port = find_port(60001)

    app = MessengerApp()
    app.run()
