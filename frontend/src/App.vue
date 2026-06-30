<template>
  <div class="layout">
    <header class="header">
      <div class="hdr">
        <div class="brand">
          <div class="brand-ico">CR</div>
          <span class="brand-name">Creative Resizer</span>
        </div>
        <nav class="nav">
          <router-link to="/" class="nav-link" :class="{ active: $route.path === '/' }">배너 생성</router-link>
          <router-link to="/jobs" class="nav-link" :class="{ active: $route.path === '/jobs' }">작업 목록</router-link>
        </nav>
        <div class="hdr-right">
          <button class="help-btn"><span class="qmark">?</span>도움말</button>
          <div class="user">
            <span class="user-av">M</span>
            <span class="user-nm">마케터님 <small>∨</small></span>
          </div>
        </div>
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
  background: #F5F6F8;
  color: #191F28;
  -webkit-font-smoothing: antialiased;
}
a { text-decoration: none; }
</style>

<style scoped>
.layout { min-height: 100vh; display: flex; flex-direction: column; }

.header {
  background: #0B0F1C;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  position: sticky; top: 0; z-index: 50; flex-shrink: 0;
}
.hdr {
  height: 56px; padding: 0 24px;
  display: flex; align-items: center; gap: 28px;
}

.brand { display: flex; align-items: center; gap: 10px; }
.brand-ico {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, #7C3AED, #3B82F6);
  color: #fff; font-size: 11px; font-weight: 800;
  display: flex; align-items: center; justify-content: center; letter-spacing: -0.5px;
}
.brand-name { font-size: 15px; font-weight: 700; color: #fff; white-space: nowrap; }

.nav { display: flex; gap: 2px; }
.nav-link {
  font-size: 14px; font-weight: 500; color: rgba(255,255,255,0.45);
  padding: 6px 14px; border-radius: 8px; transition: all 0.12s;
}
.nav-link:hover  { color: rgba(255,255,255,0.85); background: rgba(255,255,255,0.07); }
.nav-link.active { color: #fff; font-weight: 600; background: rgba(255,255,255,0.13); }

.hdr-right { margin-left: auto; display: flex; align-items: center; gap: 18px; }
.help-btn {
  display: flex; align-items: center; gap: 6px;
  background: none; border: none; cursor: pointer; font-family: inherit;
  color: rgba(255,255,255,0.45); font-size: 13px; padding: 0;
}
.help-btn:hover { color: rgba(255,255,255,0.8); }
.qmark {
  width: 17px; height: 17px; border-radius: 50%;
  border: 1.5px solid rgba(255,255,255,0.3);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700;
}
.user { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.user-av {
  width: 30px; height: 30px; border-radius: 50%;
  background: linear-gradient(135deg, #10B981, #059669);
  color: #fff; font-size: 13px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.user-nm { font-size: 13px; color: rgba(255,255,255,0.65); }
.user-nm small { font-size: 10px; }

.main { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
</style>
