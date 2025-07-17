# run.py
import uvicorn
import asyncio
import logging


async def start_api_server():
    """启动API服务器服务"""

    # 配置并启动API服务器
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=2053, reload=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    # 启动主程序
    try:
        asyncio.run(start_api_server())
    except KeyboardInterrupt:
        logging.info("程序被用户中断")
    except Exception as e:
        logging.error(f"程序运行出错: {e}")
