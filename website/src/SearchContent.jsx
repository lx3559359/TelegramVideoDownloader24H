import React from 'react';

export function SearchContent() {
  return <section className="wrap section" id="video-download-guide">
    <span className="eyebrow">Telegram / TG 视频下载指南</span>
    <h2>如何下载 Telegram 群组和频道的视频？</h2>
    <p className="section-intro">这款 Windows 桌面工具适合将账号有权访问的 Telegram 视频保存到本地。先查看<a href="#guide">安装与配置步骤</a>，再选择自动监听或按需下载。</p>
    <div className="steps">
      <article><h3>群组与频道视频自动下载</h3><p>选择并保存要监听的群组或已订阅频道，启动后台后接收新视频。需要下载旧视频时，为对应目标开启历史补抓；历史任务可以独立暂停。</p></article>
      <article><h3>按关键词和日期挑选视频</h3><p>在“视频检索”页选择目标，输入关键词或日期范围，核对结果后将选中的视频加入下载队列。支持一次选择多项，下载按单文件队列依次执行。</p></article>
      <article><h3>断点续传与本地分类保存</h3><p>中断后恢复有效断点，已完成内容去重。新任务可按同组说明中的明确片名归类；未识别到片名时按月份保存，目录由你选择。</p></article>
    </div>
    <p className="note">下载前须知：推荐 Windows 10/11 64 位 EXE 安装版，已内置运行环境，无需 Python 或 Git。首次使用需填写自己的 Telegram API 信息并登录；首次联网验证可开通 24 小时试用，后续使用需有效授权。工具无法绕过账号访问权限。</p>
  </section>;
}
