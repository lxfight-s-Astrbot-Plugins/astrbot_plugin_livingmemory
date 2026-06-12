import { defineConfig } from 'vitepress'

const repo = 'https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory'

export default defineConfig({
  title: 'LivingMemory',
  description: 'Intelligent long-term memory plugin for AstrBot',
  base: process.env.DOCS_BASE || '/astrbot_plugin_livingmemory/',
  cleanUrls: true,
  lastUpdated: true,
  ignoreDeadLinks: false,
  head: [
    ['meta', { name: 'theme-color', content: '#3d7f8f' }]
  ],
  locales: {
    root: {
      label: '简体中文',
      lang: 'zh-CN',
      title: 'LivingMemory',
      description: '为 AstrBot 打造的智能长期记忆插件',
      themeConfig: {
        nav: navZh(),
        sidebar: sidebarZh(),
        outline: {
          label: '本页目录',
          level: [2, 3]
        },
        lastUpdated: {
          text: '最后更新',
          formatOptions: {
            dateStyle: 'medium',
            timeStyle: 'short'
          }
        },
        docFooter: {
          prev: '上一页',
          next: '下一页'
        },
        editLink: {
          pattern: `${repo}/edit/master/docs/:path`,
          text: '在 GitHub 上编辑此页'
        }
      }
    },
    en: {
      label: 'English',
      lang: 'en-US',
      title: 'LivingMemory',
      description: 'Intelligent long-term memory plugin for AstrBot',
      themeConfig: {
        nav: navEn(),
        sidebar: sidebarEn(),
        outline: {
          label: 'On this page',
          level: [2, 3]
        },
        lastUpdated: {
          text: 'Last updated',
          formatOptions: {
            dateStyle: 'medium',
            timeStyle: 'short'
          }
        },
        docFooter: {
          prev: 'Previous',
          next: 'Next'
        },
        editLink: {
          pattern: `${repo}/edit/master/docs/:path`,
          text: 'Edit this page on GitHub'
        }
      }
    }
  },
  themeConfig: {
    logo: `${repo}/raw/master/logo.png`,
    siteTitle: 'LivingMemory',
    socialLinks: [
      { icon: 'github', link: repo }
    ],
    search: {
      provider: 'local'
    }
  }
})

function navZh() {
  return [
    { text: '指南', link: '/guide/getting-started' },
    { text: '功能', link: '/features' },
    { text: '架构', link: '/architecture' },
    { text: 'GitHub', link: repo }
  ]
}

function navEn() {
  return [
    { text: 'Guide', link: '/en/guide/getting-started' },
    { text: 'Features', link: '/en/features' },
    { text: 'Architecture', link: '/en/architecture' },
    { text: 'GitHub', link: repo }
  ]
}

function sidebarZh() {
  return [
    {
      text: '开始使用',
      items: [
        { text: '快速开始', link: '/guide/getting-started' },
        { text: '配置参考', link: '/configuration' },
        { text: '命令速查', link: '/commands' },
        { text: 'WebUI 管理', link: '/webui' }
      ]
    },
    {
      text: '深入了解',
      items: [
        { text: '功能说明', link: '/features' },
        { text: '技术架构', link: '/architecture' },
        { text: '部署文档站', link: '/deployment' }
      ]
    }
  ]
}

function sidebarEn() {
  return [
    {
      text: 'Get Started',
      items: [
        { text: 'Quick Start', link: '/en/guide/getting-started' },
        { text: 'Configuration', link: '/en/configuration' },
        { text: 'Commands', link: '/en/commands' },
        { text: 'WebUI', link: '/en/webui' }
      ]
    },
    {
      text: 'Deep Dive',
      items: [
        { text: 'Features', link: '/en/features' },
        { text: 'Architecture', link: '/en/architecture' },
        { text: 'Docs Deployment', link: '/en/deployment' }
      ]
    }
  ]
}
