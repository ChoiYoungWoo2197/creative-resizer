<template>
  <div class="layout">
    <header class="header">
      <div class="header-inner">
        <div class="logo">Creative Resizer</div>
        <nav class="nav">
          <router-link to="/" class="nav-item" :class="{ active: $route.path === '/' }">배너 생성</router-link>
          <router-link to="/jobs" class="nav-item" :class="{ active: $route.path === '/jobs' }">작업 목록</router-link>
          <router-link to="/spec" class="nav-item" :class="{ active: $route.path === '/spec' }">규격 관리</router-link>
        </nav>
      </div>
    </header>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #F2F4F6;
  color: #191F28;
  -webkit-font-smoothing: antialiased;
}
a { text-decoration: none; }
</style>

<style scoped>
.layout { min-height: 100vh; display: flex; flex-direction: column; }

.header {
  background: #fff;
  border-bottom: 1px solid #E5E8EB;
  position: sticky;
  top: 0;
  z-index: 50;
}
.header-inner {
  max-width: 720px;
  margin: 0 auto;
  padding: 0 24px;
  height: 62px;
  display: flex;
  align-items: center;
  gap: 36px;
}
.logo {
  font-size: 16px;
  font-weight: 800;
  color: #3182F6;
  letter-spacing: -0.5px;
  white-space: nowrap;
}
.nav { display: flex; gap: 2px; }
.nav-item {
  color: #6B7684;
  font-size: 14px;
  font-weight: 500;
  padding: 7px 12px;
  border-radius: 8px;
  transition: all 0.12s;
}
.nav-item:hover { color: #191F28; background: #F2F4F6; }
.nav-item.active { color: #3182F6; font-weight: 600; background: #EEF4FF; }

.main {
  flex: 1;
  padding: 40px 24px;
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
}
</style>
