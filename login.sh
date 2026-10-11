G=127.0.0.1:8000
J='content-type: application/json'
PW=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\r')
TOKEN=$(curl -s -XPOST $G/api/auth/login -H "$J" -d "{\"username\":\"admin\",\"password\":\"$PW\"}" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
H="Authorization: Bearer $TOKEN"
echo "token length: ${#TOKEN}"
