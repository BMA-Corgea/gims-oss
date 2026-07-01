from rich import print
# Optional: color library for terminal output
try:
    from rich import print
except ImportError:
    pass  # fallback if Rich isn't installed

def print_colored_status(breakdown: dict) -> None:
    """
    Pretty‐print each zone with colorized status (requires rich).
    Shows overrides first, followed by progress zones.
    """
    status_colors = {
        "UPLOADED": "green",
        "COMPLETE": "green",
        "PARSED": "green",
        "MANUALLY COMPLETED": "green",
        "PENDING": "yellow",
        "MISSING REQUIRED FIELDS": "red",
        "NOT UPLOADED": "red",
        "ERROR": "bold red",
        "NOTIFICATION": "cyan",
        "EXCEPTION": "orange3",
        "CANCELLED": "yellow",
        "RESOLVED": "green",
    }

    # 1) Overrides
    if "override_status" in breakdown:
        val = breakdown["override_status"]
        lines = val.split("\n") if isinstance(val, str) else [str(val)]
        for entry in lines:
            if ":" in entry:
                kind, label = entry.split(":", 1)
                kind = kind.strip().upper()
                label = label.strip()
                color = status_colors.get(kind, "white")
                print(f"[bold]{kind.title():<15}[/bold]: [{color}]{label}[/{color}]")

    # 2) Progress & other zones
    for zone, status in breakdown.items():
        if zone == "override_status":
            continue
        label = str(status).upper()
        color = status_colors.get(label, "white")
        zone_name = zone.replace("_", " ").title()
        print(f"[bold]{zone_name:<15}[/bold]: [{color}]{status}[/{color}]")