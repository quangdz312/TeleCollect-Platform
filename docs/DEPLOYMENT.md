# Triển khai và sao lưu

Cách dựng, chạy, kiểm tra, sao lưu và dừng an toàn hệ thống trên VPS
(`docker-compose.prod.yml` + `Caddyfile`). Đang chạy tại **telecollect.io.vn**.

---

## 1. Giới hạn đã biết của bản triển khai này

- **Một tiến trình backend duy nhất.** `SessionManager` (phiên teleop) và
  `TrainingJobManager` (job huấn luyện) giữ trạng thái trong RAM — thêm bản sao
  thứ hai sẽ chia đôi trạng thái đó, và khởi động lại làm mất phiên teleop đang
  chạy, đánh dấu job huấn luyện đang chạy là FAILED.
- **SQLite và tệp cục bộ.** Không có PostgreSQL, không có object storage.
- **Huấn luyện tắt mặc định** (`TRAINING_ENABLED=false`) vì image CPU chỉ cài
  `requirements.txt`, không cài `requirements-train.txt`, và máy không có GPU.
  Đánh giá thì bật riêng được bằng `EVALUATION_ENABLED=true` — rollout chạy được
  trên CPU.

## 2. Thử cục bộ trước khi đụng VPS

Chỉ chứng minh image dựng được và Caddy định tuyến đúng — không cần DNS hay chứng chỉ.

```bash
docker compose -f docker-compose.local.yml config
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
curl -f http://localhost:8080/
curl -f http://localhost:8080/health
docker compose -f docker-compose.local.yml logs --tail=200
docker compose -f docker-compose.local.yml down
```

**Không bao giờ `down -v`** — nó xoá volume chứa SQLite. Mất dữ liệu thử nghiệm
thì không sao, nhưng tập thói quen từ đây vì production tuyệt đối không được `-v`.

## 3. Dựng và khởi động trên VPS

Điều kiện: `/srv/telecollect/secrets/.env.production` đã có, điền từ
`.env.production.example`, `chmod 600`. `/srv/telecollect/data` tồn tại và
container ghi được.

```bash
cd /srv/telecollect/app

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml config --quiet     # kiểm trước khi làm gì thật

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml build

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml up -d
```

Đừng chạy `up` cho stack production ở `localhost` với giá trị domain thật —
Caddy sẽ xin chứng chỉ Let's Encrypt cho một domain chưa trỏ về máy này và hỏng.
Dùng `docker-compose.local.yml` cho mọi thứ trước khi DNS lên.

## 4. Cập nhật mã lên VPS

VPS **không có git remote hợp lệ**, nên không dùng `git pull` ở đó. Quy trình là
Git bundle:

```powershell
# Trên máy dev (PowerShell)
$Sha = (git rev-parse HEAD).Trim()
$Bundle = "$env:TEMP\telecollect-$($Sha.Substring(0,7)).bundle"
git bundle create $Bundle main
git bundle verify $Bundle
(Get-FileHash $Bundle -Algorithm SHA256).Hash.ToLowerInvariant()

scp -o IdentitiesOnly=yes -i $env:USERPROFILE\.ssh\<key> `
  $Bundle "deploy@<vps>:/tmp/release.bundle"
```

```bash
# Trên VPS — đối chiếu sha256 với máy dev trước khi fetch
cd /srv/telecollect/app
sha256sum /tmp/release.bundle
git bundle verify /tmp/release.bundle

test -z "$(git status --porcelain --untracked-files=all)"
git fetch /tmp/release.bundle refs/heads/main:refs/remotes/release-bundle/main
BUNDLE_SHA=$(git rev-parse refs/remotes/release-bundle/main)
git merge-base --is-ancestor HEAD "$BUNDLE_SHA" || { echo "STOP: khong fast-forward"; exit 1; }
git merge --ff-only "$BUNDLE_SHA"
```

Sao lưu trước, giữ image cũ để lùi được, rồi mới dựng lại:

```bash
docker tag app-backend  app-backend:rollback-<sha cũ>
docker tag app-frontend app-frontend:rollback-<sha cũ>

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml build backend frontend

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml up -d --no-deps --force-recreate backend frontend
```

Build hỏng thì **dừng, đừng recreate** — container cũ vẫn đang phục vụ.

## 5. Kiểm tra và khởi động lại

```bash
docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml ps

docker compose --env-file /srv/telecollect/secrets/.env.production \
  -f docker-compose.prod.yml logs --tail=200

curl -fsS https://telecollect.io.vn/health
```

Khởi động lại `backend` làm mất phiên teleop đang chạy (trạng thái chỉ ở RAM) và
đánh dấu job huấn luyện đang chạy là FAILED. Không đụng tới SQLite hay tệp trên
thư mục dữ liệu.

**Không bao giờ `down -v`** trên production: `-v` xoá `caddy_data`, tức vứt luôn
chứng chỉ Let's Encrypt, và Let's Encrypt có giới hạn số lần xin lại.

## 6. Quyền thư mục dữ liệu

Container backend chạy dưới `appuser`. Thư mục host gắn vào `/app/data`
(`DATA_HOST_PATH`, mặc định `/srv/telecollect/data`) phải cho user đó ghi:

```bash
sudo mkdir -p /srv/telecollect/data
sudo chown -R deploy:deploy /srv/telecollect/data
```

Nếu backend báo lỗi quyền khi ghi `/app/data`, kiểm UID thật của `appuser`
(`docker compose ... exec backend id`) so với chủ sở hữu thư mục host — đừng với
tay tới `chmod 777`.

---

## 7. Sao lưu

### Cái gì được sao lưu

| Nguồn | Cách | Đích |
|---|---|---|
| `app.db` (SQLite) | `sqlite3 .backup` — API sao lưu trực tuyến của SQLite, an toàn khi backend đang ghi | `$BACKUP_ROOT/sqlite/app-<UTC>.db` + `.sha256` |
| `episodes/`, `datasets/`, `training/`, `review/` | `rsync -a`, tăng dần, không bao giờ `--delete` | `$BACKUP_ROOT/artifacts/` + `manifest.sha256` |
| (với `--full`) cả hai | `tar czf` | `$BACKUP_ROOT/archives/telecollect-full-<ts>.tar.gz` + `.sha256` |

**Cố ý không sao lưu:** `tmp/` (vùng nháp kiểm tra upload) và mọi thứ dưới
`/srv/telecollect/secrets`.

### Bí mật phải nằm ngoài `DATA_DIR`

Đây là ranh giới **vận hành**, không phải thứ script tự bảo đảm được: bí mật
thuộc về `/srv/telecollect/secrets`, không bao giờ nằm trong
`/srv/telecollect/data`.

Bốn thư mục được sao lưu là danh sách cho phép **theo tên thư mục** — nếu một tệp
dạng thông tin xác thực lọt vào trong đó thì nó nằm trong danh sách và sẽ bị chép
theo.

Lớp phòng thủ thứ hai: `backup_staging.sh` quét bốn thư mục trước khi chép và
**từ chối chạy** (huỷ cả lần sao lưu, không im lặng bỏ qua) nếu thấy tệp hoặc thư
mục tên `.env`, `.env.*`, `secrets`, `id_rsa`, `id_ed25519`, `*.pem`, `*.key`.
Đây là bẫy dây, không phải bảo đảm — nó không bắt được bí mật nhúng trong tệp có
tên không liên quan.

### Chạy sao lưu

```bash
# Hằng ngày / trước khi triển khai
./scripts/backup_staging.sh

# Trước và sau cửa sổ chấm điểm: thêm bản lưu trữ tar.gz đầy đủ
./scripts/backup_staging.sh --full
```

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DATA_DIR` | `/srv/telecollect/data` | Nguồn |
| `BACKUP_ROOT` | `/srv/telecollect/backups` | Đích — phải nằm ngoài `DATA_DIR` |
| `RETENTION_COUNT` | `14` | Số bản SQLite giữ lại |
| `ARCHIVE_RETENTION_COUNT` | `3` | Số bản lưu trữ đầy đủ giữ lại |

Hai biến giữ lại được kiểm tra **trước khi** tạo hoặc xoá tệp nào: `0`, số âm hay
giá trị không phải số đều dừng ngay với thông báo rõ và không đụng gì. Không có
chế độ "giữ mãi mãi" — truyền một số lớn nếu muốn vậy.

Script dừng ngay (exit 1, không để lại thứ gì trông như bản sao lưu thành công)
nếu: thiếu `sqlite3`/`rsync`/`sha256sum`/`tar`/`flock`, `DATA_DIR` hoặc tệp cơ sở
dữ liệu không tồn tại, `BACKUP_ROOT` không ghi được hoặc nằm trong `DATA_DIR`,
còn dưới 512 MB trống, tìm thấy tệp nghi là bí mật, hoặc đã có lần sao lưu khác
đang chạy.

### Quyền

`app.db` chứa băm mật khẩu. Cả hai script chạy dưới `umask 077` và kết thúc bằng
một lượt `chmod` đệ quy — chỉ trong `$BACKUP_ROOT` (sao lưu) hoặc `$TARGET`
(khôi phục), **không bao giờ** đụng `DATA_DIR`.

Kiểm sau khi chạy:

```bash
find /srv/telecollect/backups -perm -o+r    # phải không in gì
stat -c '%a %n' /srv/telecollect/backups     # phải là 700
```

### Đặt lịch

```
# /etc/cron.d/telecollect-backup
0 3 * * * deploy DATA_DIR=/srv/telecollect/data BACKUP_ROOT=/srv/telecollect/backups /srv/telecollect/app/scripts/backup_staging.sh >> /var/log/telecollect-backup.log 2>&1
```

## 8. Khôi phục

**Luôn khôi phục vào một thư mục mới do bạn đặt tên. Không bao giờ ghi đè
`DATA_DIR`, không đụng stack đang chạy.**

```bash
# Từ bản SQLite + kho artifact
./scripts/restore_staging.sh \
  --sqlite-backup /srv/telecollect/backups/sqlite/app-<ts>.db \
  --artifacts-source /srv/telecollect/backups/artifacts \
  --target /srv/telecollect/restore-test/<ngày>

# Từ bản lưu trữ đầy đủ
./scripts/restore_staging.sh \
  --archive /srv/telecollect/backups/archives/telecollect-full-<ts>.tar.gz \
  --target /srv/telecollect/restore-test/<ngày>
```

Đưa bản đã khôi phục thành dữ liệu thật là việc **thủ công, có chủ đích, không
bao giờ tự động**:

```bash
# 1. Dừng stack để không có gì ghi vào giữa chừng
# 2. Đổi tên thư mục dữ liệu hiện tại sang chỗ khác — đừng xoá vội
# 3. Chuyển bản đã khôi phục và kiểm tra vào vị trí
# 4. Khởi động lại và chạy kiểm tra
```
