# -*- coding: utf-8 -*-
import os
import json
import re
import logging
import requests
from flask import Flask, request
from wechatpy import parse_message, create_reply
from wechatpy.utils import check_signature
from wechatpy.exceptions import InvalidSignatureException
from readability import Document
from lxml import html

# ==================== 初始化配置 ====================
app = Flask(__name__)

# 配置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("WeChatBot")

# 环境变量校验
REQUIRED_ENV_VARS = ['WECHAT_TOKEN', 'DEEPSEEK_API_KEY']
for var in REQUIRED_ENV_VARS:
    if not os.getenv(var):
        raise EnvironmentError(f"必须设置环境变量: {var}")

WECHAT_TOKEN = os.getenv("WECHAT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# ==================== 微信验证处理 ====================
@app.route('/', methods=['GET', 'POST'])
def handle_wechat():
    """处理微信所有请求"""
    if request.method == 'GET':
        return verify_wechat(request)
    
    try:
        return process_message(request)
    except Exception as e:
        logger.error(f"全局异常: {str(e)}", exc_info=True)
        return create_reply("服务器处理消息时发生错误").render()

def verify_wechat(req):
    """微信服务器验证"""
    signature = req.args.get('signature', '')
    timestamp = req.args.get('timestamp', '')
    nonce = req.args.get('nonce', '')
    echostr = req.args.get('echostr', '')

    logger.info(
        "验证请求参数:\n"
        f"Token: {WECHAT_TOKEN}\n"
        f"Signature: {signature}\n"
        f"Timestamp: {timestamp}\n"
        f"Nonce: {nonce}"
    )

    try:
        check_signature(WECHAT_TOKEN, signature, timestamp, nonce)
        logger.info("✅ 验证成功")
        return echostr
    except InvalidSignatureException as e:
        logger.error(f"❌ 验证失败: {str(e)}")
        return '验证失败', 403

# ==================== 消息处理逻辑 ====================
def process_message(req):
    """处理用户消息"""
    try:
        raw_data = req.data.decode('utf-8')
        logger.debug(f"原始请求数据:\n{raw_data}")
        
        msg = parse_message(raw_data)
        logger.info(f"解析消息类型: {msg.type} 内容摘要: {str(msg)[:200]}...")

        content = extract_content(msg)
        if not content or len(content) < 50:
            return create_reply("未获取到有效内容").render()

        analysis = analyze_content(content[:3000])
        return generate_reply(analysis)

    except Exception as e:
        logger.error(f"消息处理异常: {str(e)}", exc_info=True)
        return create_reply("消息处理出错").render()

def extract_content(msg):
    """内容提取"""
    if msg.type == 'text':
        url_match = re.search(r'https?://\S+', msg.content)
        return fetch_web_content(url_match.group(0)) if url_match else msg.content
    elif msg.type == 'link':
        return fetch_web_content(msg.url)
    else:
        return ""

def fetch_web_content(url):
    """网页内容抓取"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://mp.weixin.qq.com/'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        # 百度百家号解析
        if 'baijiahao.baidu.com' in url:
            tree = html.fromstring(response.text)
            content_nodes = tree.xpath('//div[contains(@class,"article-content")]//text()') or tree.xpath('//div[@id="article"]//text()')
            return '\n'.join(content_nodes).strip()[:3000]

        # 通用解析
        return Document(response.text).summary()[:3000]
    except Exception as e:
        logger.error(f"网页抓取失败: {str(e)}")
        raise

def analyze_content(text):
    """调用DeepSeek API"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "严格按JSON格式输出：{\"score\":1-100整数, \"analysis\":\"分析内容\", \"details\":[\"要点1\",\"要点2\"]}。使用转义双引号。"
            },
            {"role": "user", "content": text}
        ]
    }
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=25
        )
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"API调用失败: {str(e)}")
        raise

def generate_reply(analysis):
    """生成回复"""
    try:
        # 清洗JSON
        cleaned = re.sub(r'[\u200b-\u200f]', '', analysis)  # 移除零宽空格
        cleaned = re.sub(r'^[^{]*', '', cleaned)            # 移除开头无效字符
        data = json.loads(cleaned)
        
        # 构造回复
        score = data['score']
        color = "00c853" if score >=85 else "ffd600" if score >=65 else "d50000"
        return create_reply([{
            'title': f"📊 可信度评分：{score}/100",
            'description': f"{data['analysis']}\n\n🔍 关键点：\n• " + '\n• '.join(data['details']),
            'picurl': f"https://fakeimg.pl/600x400/{color}/fff/?text={score}分"
        }]).render()
    except Exception as e:
        logger.error(f"回复生成失败: {str(e)}")
        return create_reply("生成回复时发生错误").render()

# ==================== 健康检查接口 ====================
@app.route('/health')
def health_check():
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 80)))