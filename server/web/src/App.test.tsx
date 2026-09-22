import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import App from './App';

describe('App 壳冒烟（renderToString，node 环境）', () => {
  it('无项目时渲染顶栏与空态', () => {
    const html = renderToString(<App />);
    expect(html).toContain('易标投标工具箱');
    expect(html).toContain('标书查重');
    expect(html).toContain('还没有项目');
    expect(html).toContain('新建项目');
  });

  it('顶栏含项目选择器与 P4 导航占位', () => {
    const html = renderToString(<App />);
    expect(html).toContain('project-select');
    expect(html).toContain('废标项检查');
  });
});
