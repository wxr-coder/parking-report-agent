from pathlib import Path

from app.services.metrics import MetricsResult


def create_payment_method_chart(metrics: MetricsResult, output_path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    labels = list(metrics.payment_methods.keys())
    values = list(metrics.payment_methods.values())

    # Matplotlib may expose distro-installed Noto CJK fonts under regional
    # family names (for example Noto Sans CJK JP on Ubuntu) even when the font
    # covers Simplified Chinese glyphs. Keep several common names before the
    # DejaVu fallback so chart labels render correctly in local and Docker runs.
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Sans CJK TC",
        "Noto Sans CJK HK",
        "Noto Sans CJK KR",
        "SimHei",
        "Microsoft YaHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=160)
    colors = ["#2f80ed", "#27ae60", "#f2c94c", "#eb5757", "#9b51e0", "#56ccf2"]
    ax.bar(labels, values, color=colors[: len(labels)])
    ax.set_title("支付方式交易笔数分布")
    ax.set_ylabel("交易笔数")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    for index, value in enumerate(values):
        ax.text(index, value, str(value), ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path
