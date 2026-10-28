import json
import shutil
import socket
import sys
import uuid
from logging import exception
from pathlib import Path
from typing import ClassVar

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Log,
    MaskedInput,
)
from textual.widgets.button import ButtonVariant

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

class ConfirmScreen(ModalScreen[bool]):
    def __init__(
        self, question: str,
        confirm_label: str = "Confirm", confirm_variant: ButtonVariant = "error",
        cancel_label: str = "Cancel", cancel_variant: ButtonVariant = "default"
    ) -> None:
        super().__init__()
        self.question = question
        self.confirm_label = confirm_label
        self.confirm_variant: ButtonVariant = confirm_variant
        self.cancel_label = cancel_label
        self.cancel_variant: ButtonVariant = cancel_variant

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(self.question, id="dialog-question"),
            Button(self.confirm_label, variant=self.confirm_variant, id="dialog-confirm-button"),
            Button(self.cancel_label, variant=self.cancel_variant, id="dialog-cancel-button"),
            id="dialog-grid",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "dialog-confirm-button")
  
class CreateChatScreen(ModalScreen[tuple[str, str] | None]):
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Enter new chat name and uuid:", id="create-chat-label"),
            Input(placeholder="Enter chat name", id="input-name"),
            MaskedInput(template="HHHHHHHH-HHHH-HHHH-HHHH-HHHHHHHHHHHH;_", id="input-uuid"),
            Button("Create", variant="success", id="dialog-confirm-button"),
            Button("Cancel", variant="default", id="dialog-cancel-button"),
            id="create-chat-dialog-grid"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()

        if event.button.id == "dialog-cancel-button":
            self.dismiss(None)
            return

        name = self.query_one("#input-name", Input).value.strip()
        uuid_val = self.query_one("#input-uuid", MaskedInput).value.strip()

        if not name:
            self.notify("Chat name cannot be empty!", severity="error")
            return

        uuid_input = self.query_one("#input-uuid", MaskedInput)
        if not uuid_input.is_valid:
            self.notify("UUID cannot be empty!", severity="error")
            return
        
        self.dismiss((name, uuid_val))
    
class RenameChatScreen(ModalScreen[str | None]):
    def compose(self) -> ComposeResult:
        yield Input(placeholder="Enter new chat name, type nothing and press Enter to cancel", id="dialog-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.input.value.strip() or None)

class MessengerApp(App):
    CSS_PATH = styles_path / "style.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("n", "create_chat", "Create Chat", show=True),
        Binding("r", "rename_chat", "Rename Chat", show=True),
        Binding("d", "delete_chat", "Delete Chat", show=True),
        Binding("c", "clear_chat_history", "Clear Chat History", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield ListView(id="chat-list")

            with Vertical(id="chat-area"):
                yield Log(id="output", auto_scroll=True, highlight=True)
                yield Input(placeholder="Enter a message...", id="input")

        yield Footer()

    def on_mount(self) -> None:
        with open(config_path, "r", encoding="utf-8") as f:
            self.user_config = json.load(f)

        self.query_one("#chat-list").border_title = "root\\data\\chats"
        self.query_one("#output").border_title    = "no chat selected"

        self.known_chats: dict[str, Path] = {}
        self.chat_names:  dict[str, str]  = {}
        self._last_mtime: float = 0.0
        self._last_config_mtime: float = 0.0

        self.load_chats()
        
        self.set_interval(0.5, self.load_chats)
        self.set_interval(0.5, self.refresh_history)
        self.set_interval(0.5, self.refresh_user_config)
        
        self.title = chat_name_selected
        self.refresh_user_config()

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
            item = ListItem(Label(truncate_str(chat_name, 28)), id=_widget_id(chat_id))
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
        log = self.query_one("#output", Log)
        
        if current_chat_folder is None:
            if log.border_title != "no chat selected":
                log.border_title = "no chat selected"
                log.clear()
            return
            
        history = current_chat_folder / "history.txt"
        if not history.is_file():
            log.clear()
            return
                
        mtime = history.stat().st_mtime
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime

        self.query_one("#output").border_title = f"root\\data\\chats\\{current_chat_folder.name}\\history.txt"

        log.clear()
        with history.open(encoding="utf-8") as f:
            for line in f:
                log.write_line(line.strip("\n"))
    def refresh_user_config(self) -> None:
        if not config_path.exists():
            return

        mtime = config_path.stat().st_mtime
        if mtime == self._last_config_mtime:
            return
        self._last_config_mtime = mtime

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.user_config = json.load(f)
            user_name = self.user_config.get("name", "user")
            self.sub_title = f" @{user_name}"
        except Exception:
            pass

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
        self.title = truncate_str(self.chat_names[chat_id], 28)
        self._last_mtime = 0.0
        self.refresh_history()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        output = self.query_one("#output", Log)
        if event.input.value == "":
            return
        if current_chat_folder is None:
            self.notify("No chat selected.")
            return

        user_name = self.user_config.get("name", "user")
        safe_name = escape(user_name)
        safe_text = escape(event.value)

        output.write_line(f"{safe_name}: {safe_text}")

        with (current_chat_folder / "history.txt").open("a", encoding="utf-8") as f:
            f.write(f"{user_name}: {event.value}\n")

        event.input.value = ""

    def action_create_chat(self) -> None:
        self.push_screen(CreateChatScreen(), callback=self.on_create_chat_done)
        
    def on_create_chat_done(self, result: tuple[str, str] | None) -> None:
        if not result:
            return

        chat_name, provided_uuid = result

        try:
            uuid.UUID(provided_uuid)
        except ValueError:
            self.notify("Invalid UUID format!", severity="error")
            return

        folder = Path(chats_path) / provided_uuid 
        if folder.exists():
            self.notify(f"Chat with UUID {provided_uuid[:8]} already exists!", severity="error")
            return

        try:
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "config.json").open("w", encoding="utf-8") as f:
                json.dump({"name": chat_name}, f, ensure_ascii=False, indent=4)
            (folder / "history.txt").touch()
        except Exception as e:
            self.notify(f"Error creating chat: {e}", severity="error")
            return

        self.load_chats()
        self.notify(f"Created chat: {chat_name} ({provided_uuid[:8]})")
        
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

    def action_delete_chat(self) -> None:
        if current_chat_folder is None:
            self.notify("No chat selected.")
            return
    
        chat_id = current_chat_folder.name
        name = self.chat_names.get(chat_id, chat_id)
        short_name = truncate_str(name, 35)
        self.push_screen(
            ConfirmScreen(
                question=f'Are you sure you want to delete chat\n"{short_name}"?', 
                confirm_label="Delete",
            ), 
            callback=self.on_delete_done
        )
    def on_delete_done(self, confirmed: bool | None) -> None:
        global current_chat_folder

        if not confirmed or current_chat_folder is None:
            return

        chat_id = current_chat_folder.name
        name    = self.chat_names.get(chat_id, chat_id)
        folder  = current_chat_folder

        try:
            shutil.rmtree(folder)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return

        self.known_chats.pop(chat_id, None)
        self.chat_names.pop(chat_id, None)

        for item in self.query_one("#chat-list", ListView).children:
                if item.id == _widget_id(chat_id):
                    item.remove()
                    break
        
        current_chat_folder = None
        self._last_mtime    = 0.0
        self.title          = chat_name_selected
        
        self.refresh_history()
        self.notify(f"Deleted chat: {name}")

    def action_clear_chat_history(self) -> None:
        if current_chat_folder is None:
            self.notify("No chat selected.")
            return

        chat_id = current_chat_folder.name
        name = self.chat_names.get(chat_id, chat_id)
        short_name = truncate_str(name, 35)

        self.push_screen(
            ConfirmScreen(
                question=f'Are you sure you want to clear history of chat\n"{short_name}"?',
                confirm_label="Clear",
            ), 
            callback=self.on_clear_done
        )
    def on_clear_done(self, confirmed: bool | None) -> None:
        if not confirmed or current_chat_folder is None:
            return

        history_file = current_chat_folder / "history.txt"
        try:
            history_file.write_text("", encoding="utf-8")
        except Exception as e:
            self.notify(f"Error {e}", severity="error")
            return

        self._last_mtime = 0.0
        self.refresh_history()
        self.notify("Chat history cleared.")


def truncate_str(text: str, limit: int = 30) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."
 
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
            
    if not file.get("name"):
        file["name"] = "user"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(file, f, ensure_ascii=False, indent=4)
            
    if not file.get("ip"):
        file["ip"] = get_my_local_ip()
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
