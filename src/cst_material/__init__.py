"""Offline CST geometry and material automation framework."""

__version__ = "0.1.0"

def process_project(input_cst, output_cst, **kwargs):
    """单一业务接口：输入 CST 工程，输出新的 CST 工程。"""
    from cst_material.platform_pipeline import process_project as run

    return run(input_cst, output_cst, **kwargs)
