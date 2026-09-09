/// <reference types="vite/client" />

// VITE_ 前缀环境变量在构建时注入（.env 或构建命令），这里补充项目用到的键的类型
interface ImportMetaEnv {
  /** 后端 API 绝对地址（生产部署用），缺省为同源相对路径，走 Nginx 反代 */
  readonly VITE_API_BASE?: string
  /** 后端 API Token：注入后所有请求自动携带 X-API-Token 头 */
  readonly VITE_API_TOKEN?: string
}
