"""Visualization tools: live real-time dashboard and offline session plotting."""

from wibreathe.viz.live_plot import LiveDashboard
from wibreathe.viz.offline_plots import generate_offline_plots

__all__ = ["LiveDashboard", "generate_offline_plots"]
