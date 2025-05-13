# -*- coding: utf-8 -*-
import os
import time
import hashlib
import requests
from flask import Flask, request
from wechatpy import parse_message, create_reply
from wechatpy.utils import check_signature
from wechatpy.exceptions import InvalidSignatureException
from readability import Document

# ==================== 初始化配置 ====================
app = Flask(__name__)

# 从环境变量获取配置（Railway后台设置）
WECHAT_TOKEN = os.getenv("WECHAT_TOKEN")         # 微信公众号Token
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") # DeepSeek的API密钥

# ==================== 微信验证处理 ====================
@app.route('/', methods=['GET', 'POST'])
def handle_wechat():
    """处理所有微信请求"""
    # GET请求 - 微信服务器验证
    if request.method == 'GET':
        return verify_wechat(request)
    
    # POST请求 - 处理用户消息
    try:
        return process_message(request)
    except Exception as e:
        return create_reply(f"系统错误：{str(e)}").render()

def verify_wechat(req):
    """微信服务器验证"""
    signature = req.args.get('signature', '')
    timestamp = req.args.get('timestamp', '')
    nonce = req.args.get('nonce', '')
    echostr = req.args.get('echostr', '')
    
    # 打印调试信息
    print("\n===== 收到微信验证请求 =====")
    print(f"Token: {WECHAT_TOKEN}")
    print(f"Signature: {signature}")
    print(f"Timestamp: {timestamp}")
    print(f"Nonce: {nonce}")
    
    try:
        check_signature(WECHAT_TOKEN, signature, timestamp, nonce)
        print("✅ 验证成功")
        return echostr
    except InvalidSignatureException as e:
        print(f"❌ 验证失败: {str(e)}")
        return '验证失败', 403

# ==================== 消息处理逻辑 ====================
def process_message(req):
    """处理用户消息"""
    raw_data = req.data.decode('utf-8')
    msg = parse_message(raw_data)
    
    # 获取消息内容
    if msg.type == 'text':
        content = msg.content
    elif msg.type == 'link':
        content = fetch_web_content(msg.url)
    else:
        return create_reply("暂不支持此类型消息").render()
    
    # 调用DeepSeek分析
    analysis = analyze_content(content)
    return generate_reply(analysis)

def fetch_web_content(url):
    """抓取网页正文"""
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    return Document(response.text).summary()

def analyze_content(text):
    """调用DeepSeek分析"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{
            "role": "system",
            "content": "请分析新闻真实性，返回严格JSON格式：{'score':1-100, 'analysis':'分析内容','details':['要点1','要点2','要点3']}"
        },{
            "role": "user",
            "content": text[:3000]
        }]
    }
    response = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers)
    return response.json()['choices'][0]['message']['content']

def generate_reply(analysis):
    """生成微信回复"""
    try:
        data = eval(analysis)
        score = data.get('score', 0)
        color = "00c853" if score >=85 else "ffd600" if score >=65 else "d50000"
        articles = [{
            'title': f"📊 可信度评分：{score}/100",
            'description': f"{data.get('analysis','')}\n\n🔍 关键点：\n• " + '\n• '.join(data.get('details',[])),
            'picurl': f"https://fakeimg.pl/600x400/{color}/fff/?text={score}分"
        }]
        return create_reply(articles).render()
    except:
        return create_reply("分析结果解析失败").render()

# ==================== 启动服务 ====================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))