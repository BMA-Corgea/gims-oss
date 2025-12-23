import sys, types

# Provide lightweight stubs for the textual library if it's missing
if 'textual' not in sys.modules:
    textual = types.ModuleType('textual')
    sys.modules['textual'] = textual
    textual.app = types.ModuleType('textual.app')
    sys.modules['textual.app'] = textual.app
    class DummyApp:
        def __init__(self, *a, **k): pass
        def run(self): pass
    class ComposeResult: pass
    textual.app.App = DummyApp
    textual.app.ComposeResult = ComposeResult
    textual.binding = types.ModuleType('textual.binding')
    sys.modules['textual.binding'] = textual.binding
    class Binding:
        def __init__(self, *a, **k): pass
    textual.binding.Binding = Binding
    textual.containers = types.ModuleType('textual.containers')
    sys.modules['textual.containers'] = textual.containers
    textual.containers.Container = object
    textual.widgets = types.ModuleType('textual.widgets')
    sys.modules['textual.widgets'] = textual.widgets
    class Widget: pass
    class ListView:
        class Selected:
            def __init__(self, item=None):
                self.item = item
        def __init__(self, *a, **k): pass
    class ListItem(Widget):
        def __init__(self, *a, **k): pass
    class Label(Widget):
        def __init__(self, renderable=None):
            self.renderable = renderable
        def query_one(self, cls):
            return self
    textual.widgets.DataTable = Widget
    textual.widgets.Footer = Widget
    textual.widgets.Header = Widget
    textual.widgets.ListView = ListView
    textual.widgets.ListItem = ListItem
    textual.widgets.Label = Label
    textual.screen = types.ModuleType('textual.screen')
    sys.modules['textual.screen'] = textual.screen
    textual.screen.ModalScreen = object
