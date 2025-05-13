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
from selectolax.parser import HTMLParser

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
        # 解析原始数据
        raw_data = req.data.decode('utf-8')
        logger.debug(f"原始请求数据:\n{raw_data}")
        
        msg = parse_message(raw_data)
        logger.info(f"解析消息类型: {msg.type} 内容: {str(msg)[:200]}...")

        # 内容提取逻辑
        content = ""
        if msg.type == 'text':
            # 检测文本中的URL
            url_match = re.search(r'https?://\S+', msg.content)
            if url_match:
                url = url_match.group(0)
                logger.info(f"检测到文本链接: {url}")
                content = fetch_web_content(url)
            else:
                content = msg.content
        elif msg.type == 'link':
            logger.info(f"解析链接消息: {msg.url}")
            content = fetch_web_content(msg.url)
        else:
            return create_reply("暂不支持此消息类型").render()

        # 内容有效性检查
        if not content or len(content) < 50:
            logger.warning("内容过短或无有效信息")
            return create_reply("未获取到有效内容").render()

        # 调用AI分析
        try:
            analysis = analyze_content(content[:3000])  # 限制长度
            logger.debug(f"原始分析结果:\n{analysis}")
        except Exception as e:
            logger.error(f"AI分析失败: {str(e)}")
            return create_reply("分析服务暂时不可用").render()

        # 生成回复
        return generate_reply(analysis)

    except Exception as e:
        logger.error(f"消息处理异常: {str(e)}", exc_info=True)
        return create_reply("消息处理出错").render()

def fetch_web_content(url):
    """增强版网页内容抓取"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://mp.weixin.qq.com/',
        'Accept-Language': 'zh-CN,zh;q=0.9'
    }
    
    try:
        # 带重试的请求
        for retry in range(2):
            try:
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if retry == 1:
                    raise
                logger.warning(f"请求失败重试中... ({str(e)})")
        else:
            raise RuntimeError("请求失败")

        # 微信文章专用解析
        tree = HTMLParser(response.text)
        
        # 方案1：微信官方文章结构
        wechat_content = tree.css_first('#js_content')
        if wechat_content:
            text = wechat_content.text(separator='\n', strip=True)
            return text
        
        # 方案2：通用正文提取
        body = tree.body
        if body:
            # 移除无用标签
            for tag in body.css('script, style, noscript, iframe, button'):
                tag.decompose()
            return body.text(separator='\n', strip=True)
        
        # 最终回退方案
        return response.text[:3000]

    except Exception as e:
        logger.error(f"网页抓取失败: {str(e)}")
        raise RuntimeError("内容获取失败，请检查链接有效性")

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
                "content": "请严格按JSON格式输出分析结果，字段包括：score(1-100数字)、analysis(分析文本)、details(分析要点列表)"
            },
            {
                "role": "user",
                "content": f"请分析以下内容：\n{text}"
            }
        ]
    }
    
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=20
        )
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"API调用失败: {str(e)}")
        raise RuntimeError("分析服务请求失败")

def generate_reply(analysis):
    """增强版回复生成"""
    try:
        # 深度清洗JSON
        cleaned = analysis.strip()
        cleaned = re.sub(r"'", '"', cleaned)  # 单引号转双引号
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)  # 修复末尾逗号
        cleaned = re.sub(r'(?<!\\)"', r'\"', cleaned)  # 转义未处理引号
        cleaned = re.sub(r'[\x00-\x1F]', '', cleaned)  # 移除控制字符
        
        data = json.loads(cleaned)
        
        # 数据校验
        if not all(k in data for k in ('score', 'analysis', 'details')):
            raise ValueError("缺少必要字段")
        if not isinstance(data['details'], list):
            raise ValueError("details应为列表")

        # 构造回复
        score = int(data['score'])
        color = "00c853" if score >=85 else "ffd600" if score >=65 else "d50000"
        
        reply = create_reply([
            {
                'title': f"📊 可信度评分：{score}/100",
                'description': f"{data['analysis']}\n\n🔍 关键点：\n• " + '\n• '.join(data['details']),
                'picurl': f"https://fakeimg.pl/600x400/{color}/fff/?text={score}分"
            }
        ]).render()
        
        logger.debug(f"生成回复XML:\n{reply}")
        return reply
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {str(e)}\n清洗后内容: {cleaned}")
        return create_reply("分析结果格式异常").render()
    except Exception as e:
        logger.error(f"回复生成失败: {str(e)}")
        return create_reply("生成回复时发生错误").render()

# ==================== 启动服务 ====================
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)