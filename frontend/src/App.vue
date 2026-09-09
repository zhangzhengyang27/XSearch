<template>
  <div class="app">
    <header class="header">
      <div class="brand" @click="$router.push('/search')">
        <span class="logo">X</span>Search
      </div>
      <el-menu mode="horizontal" :default-active="$route.path" router :ellipsis="false" class="nav">
        <el-menu-item index="/search">搜索</el-menu-item>
        <el-menu-item index="/news">新闻</el-menu-item>
        <el-menu-item index="/ai">AI</el-menu-item>
        <el-menu-item index="/rankings">榜单</el-menu-item>
        <el-menu-item index="/stats">数据概览</el-menu-item>
        <el-menu-item index="/crawl">采集管理</el-menu-item>
      </el-menu>
    </header>

    <main class="main">
      <router-view />
    </main>

    <footer class="footer">
      Scrapy 2.13 + Playwright + Elasticsearch · 数据仅供学习交流
    </footer>
  </div>
</template>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 0 32px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand {
  font-size: 22px;
  font-weight: 700;
  cursor: pointer;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.logo { color: var(--el-color-primary); }
.nav { flex: 1; border-bottom: none !important; }
.main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
.footer {
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 20px 0 28px;
}
</style>
