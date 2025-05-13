# -*- coding: utf-8 -*-
import os
import json
import logging
import re
import requests
from flask import Flask, request
from wechatpy import parse_message, create_reply
from wechatpy.utils import check_signature
from wechatpy.exceptions import InvalidSignatureException
from readability import Document

# ==================== 初始化配置 ====================
app = Flask(__name__)

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WeChatBot")

# 强制检查环境变量
required_env_vars = ['WECHAT_TOKEN', 'DEEPSEEK_API_KEY']
for var in required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"必须设置环境变量: {var}")

WECHAT_TOKEN = os.getenv("WECHAT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# ==================== 微信验证处理 ====================
@app.route('/', methods=['GET', 'POST'])
def handle_wechat():
    """处理所有微信请求"""
    if request.method == 'GET':
        return verify_wechat(request)
    
    try:
        return process_message(request)
    except Exception as e:
        logger.error(f"全局异常捕获: {str(e)}", exc_info=True)
        return create_reply("服务器处理消息时发生错误").render()

def verify_wechat(req):
    """微信服务器验证"""
    signature = req.args.get('signature', '')
    timestamp = req.args.get('timestamp', '')
    nonce = req.args.get('nonce', '')
    echostr = req.args.get('echostr', '')
    
    logger.info(
        "\n===== 微信验证请求 ====="
        f"\nRequest args: {dict(req.args)}"
        f"\nToken: {WECHAT_TOKEN}"
        f"\nSignature: {signature}"
        f"\nTimestamp: {timestamp}"
        f"\nNonce: {nonce}"
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
        logger.info(f"\n===== 原始请求数据 =====\n{raw_data}")
        
        msg = parse_message(raw_data)
        logger.info(f"\n===== 解析后的消息 =====\n{msg.__dict__}")
        
        # 处理消息内容
        content = None
        if msg.type == 'text':
            # 增强URL检测逻辑
            url_match = re.search(r'https?://\S+', msg.content)
            if url_match:
                target_url = url_match.group(0)
                logger.info(f"检测到文本中的链接: {target_url}")
                try:
                    content = fetch_web_content(target_url)
                    logger.info(f"网页内容摘要: {content[:200]}...")
                except Exception as e:
                    logger.error(f"网页解析失败: {str(e)}")
                    return create_reply("无法解析该网页内容").render()
            else:
                content = msg.content
                logger.info(f"收到文本消息: {content[:200]}...")
        elif msg.type == 'link':
            logger.info(f"尝试解析链接: {msg.url}")
            try:
                content = fetch_web_content(msg.url)
                logger.info(f"网页内容摘要: {content[:200]}...")
            except Exception as e:
                logger.error(f"网页解析失败: {str(e)}")
                return create_reply("无法解析该网页内容").render()
        else:
            return create_reply("暂不支持此类型消息").render()
        
        if not content:
            return create_reply("未获取到有效内容").render()
        
        # 调用DeepSeek分析
        try:
            analysis_result = analyze_content(content)
            logger.info(f"\n===== DeepSeek原始响应 =====\n{analysis_result}")
        except Exception as e:
            logger.error(f"DeepSeek API调用失败: {str(e)}")
            return create_reply("分析服务暂时不可用").render()
        
        return generate_reply(analysis_result)
    
    except Exception as e:
        logger.error(f"消息处理异常: {str(e)}", exc_info=True)
        return create_reply("消息处理出错").render()

def fetch_web_content(url):
    """抓取网页正文（带User-Agent和重试机制）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        doc = Document(response.text)
        return doc.summary()
    except Exception as e:
        raise RuntimeError(f"网页抓取失败: {str(e)}")

def analyze_content(text):
    """调用DeepSeek分析（带严格JSON格式要求）"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "请严格按以下JSON格式输出分析结果：{'score':1-100数字, 'analysis':'分析文本', 'details':['要点1','要点2','要点3']}"
            },
            {
                "role": "user",
                "content": text[:3000]
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
        raise RuntimeError(f"API调用失败: {str(e)}")

def generate_reply(analysis):
    """生成微信回复（带严格JSON清洗）"""
    try:
        # 清理Markdown代码块
        cleaned = re.sub(r'```json|```', '', analysis).strip()
        # 修复常见JSON格式错误
        cleaned = re.sub(r',\s*]', ']', cleaned)  # 修复末尾逗号
        cleaned = re.sub(r',\s*}', '}', cleaned)
        
        data = json.loads(cleaned)
        if not all(key in data for key in ('score', 'analysis', 'details')):
            raise ValueError("返回JSON字段缺失")
        
        # 验证数据类型
        if not isinstance(data['score'], int) or not (1 <= data['score'] <= 100):
            raise ValueError("score值无效")
        
        score = data['score']
        color = "00c853" if score >=85 else "ffd600" if score >=65 else "d50000"
        
        articles = [{
            'title': f"📊 可信度评分：{score}/100",
            'description': (
                f"{data['analysis']}\n\n"
                "🔍 关键点：\n• " + '\n• '.join(data['details'])
            ),
            'picurl': f"https://fakeimg.pl/600x400/{color}/fff/?text={score}分"
        }]
        
        logger.info("成功生成图文回复")
        return create_reply(articles).render()
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {str(e)}\n清理后内容: {cleaned}")
        return create_reply("分析结果格式异常").render()
    except Exception as e:
        logger.error(f"回复生成失败: {str(e)}")
        return create_reply("生成回复时发生错误").render()

# ==================== 启动服务 ====================
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)