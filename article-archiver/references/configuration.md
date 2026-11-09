# 配置

## 位置与合并

- 个人配置：`$XDG_CONFIG_HOME/article-archiver/config.json`；`XDG_CONFIG_HOME` 未设置、为空或不是绝对路径时，使用 `~/.config/article-archiver/config.json`。
- 项目配置：从 `--cwd` 向上找到的最近一个 `.article-archiver.json`。
- 生效配置：默认值依次被个人配置、项目配置覆盖，对象按键递归合并。

修改个人配置前必须取得确认；不要自动修改 `.gitignore`。

默认配置：

```json
{
  "version": 1,
  "output_dir": "articles",
  "assets": {
    "mode": "download",
    "directory": "assets"
  }
}
```

| 键 | 取值 | 含义 |
| --- | --- | --- |
| `output_dir` | 非空字符串，可用 `~` | Markdown 输出目录。相对路径写在项目配置中时相对于该配置文件所在目录，否则相对于 `--cwd` |
| `assets.mode` | `download`、`remote`、`omit` | 下载资源、保留远程 URL、不保留资源 |
| `assets.directory` | 非空字符串 | 资源根目录，相对于 `output_dir` |

文章保存为 `<output_dir>/<name>.md`；`assets.mode` 为 `download` 时，资源保存到 `<output_dir>/<assets.directory>/<name>/`。

## 命令

所有命令出错时向 stderr 输出中文错误并以 `2` 退出；`plan` 另用 `1` 表示存在冲突。

### resolve

```bash
python3 <skill-dir>/scripts/archive_config.py resolve --cwd /path/to/project
```

输出 JSON：

- `config`：生效配置。
- `configured`：个人配置或项目配置至少存在一个时为 `true`；为 `false` 时按首次使用处理。
- `personal_config`：`{"path": ..., "exists": ...}`。
- `project_config`：生效的项目配置路径，没有时为 `null`。

### init

```bash
python3 <skill-dir>/scripts/archive_config.py init --scope project --cwd /path/to/project \
  --output-dir articles --assets download --assets-directory assets
```

在 `--cwd` 写入完整的项目配置；`--scope personal` 写入个人配置。目标已存在时拒绝替换，只有用户明确确认后才能加 `--force`。

### set

```bash
python3 <skill-dir>/scripts/archive_config.py set --scope project --cwd /path/to/project \
  assets.mode remote
```

只能设置 `output_dir`、`assets.mode`、`assets.directory`，值一律按字符串处理。项目范围更新从 `--cwd` 向上找到的最近项目配置，没有时在 `--cwd` 新建。

### plan

```bash
python3 <skill-dir>/scripts/archive_config.py plan --cwd /path/to/project --name "原标题"
```

输出 JSON：

- `name`：清理后的归档文件名，不含 `.md`。
- `truncated`：`name` 是否被截断。
- `markdown`：文章文件的绝对路径。
- `assets_dir`：资源目录的绝对路径；`assets.mode` 不是 `download` 时为 `null`。
- `conflicts`：已存在的目标路径。

清理规则：去掉末尾的 `.md`；空白折叠为单个空格；`\ / : * ? " < > |` 和控制字符替换为 `-`；去掉首尾的点和空白，避免生成隐藏文件；超过 200 字节（UTF-8）时在字符边界截断。清理后为空时以 `2` 退出。

退出码：`0` 无冲突，`1` 存在冲突，`2` 配置或名称无效。

脚本以原子方式写入 JSON。
