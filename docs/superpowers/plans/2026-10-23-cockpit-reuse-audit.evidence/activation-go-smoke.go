// 固定本地账号 fixture 核验保存、应用与选卡，不连接真实供应商。
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "net/http/httptest"
    "os"
    "path/filepath"
    "strings"
    "testing"

    "github.com/gin-gonic/gin"
    coreauth "github.com/router-for-me/CLIProxyAPI/v7/sdk/cliproxy/auth"
    executor "github.com/router-for-me/CLIProxyAPI/v7/sdk/cliproxy/executor"
)

func TestActivationTransactionSmoke(t *testing.T) {
    data := `{"accounts":[{"id":"a","authId":"a.json","authKind":"oauth","planRank":3},{"id":"b","authId":"b.json","authKind":"oauth","planRank":1}],"apiKeys":[],"routingStrategy":"plan_high_first","customRoutingRules":[]}`
    file := filepath.Join(t.TempDir(), "manifest.json")
    if err := os.WriteFile(file, []byte(data), 0600); err != nil { t.Fatal(err) }
    m, err := loadManifest(file)
    if err != nil { t.Fatal(err) }
    restarts := 0
    gin.SetMode(gin.TestMode)
    s := &relayServer{manifest:m, policy:&requestPolicy{}, adminToken:"fixture-admin", manifestAdmin:&manifestAdminStore{path:file}, restartService:func(){ restarts++ }}
    router := s.router()
    request := func(method, path, body, revision string) *httptest.ResponseRecorder {
        req := httptest.NewRequest(method,path,strings.NewReader(body))
        req.Header.Set("Authorization","Bearer fixture-admin")
        req.Header.Set("If-Match",revision)
        response := httptest.NewRecorder()
        router.ServeHTTP(response,req)
        return response
    }
    selector := buildCoreAuthSelector(nil,&cockpitSelector{manifest:m},m,nil)
    pick := func() string {
        auth, err := selector.Pick(context.Background(),"codex","fixture-model",executor.Options{},[]*coreauth.Auth{{ID:"a.json"},{ID:"b.json"}})
        if err != nil || auth == nil { t.Fatalf("selection failed: %v",err) }
        return auth.ID
    }
    before := pick()
    candidate := strings.Replace(data,"plan_high_first","plan_low_first",1)
    saved := request(http.MethodPut,"/admin/v1/manifest",candidate,manifestETag([]byte(data)))
    if saved.Code != 202 { t.Fatalf("save failed: %d",saved.Code) }
    afterSave := pick()
    status := request(http.MethodGet,"/admin/v1/policy-runtime","","")
    applied := request(http.MethodPost,"/admin/v1/policy-apply","",saved.Header().Get("ETag"))
    afterApply := pick()
    stale := request(http.MethodPost,"/admin/v1/policy-apply","",manifestETag([]byte(data)))
    output,_ := json.Marshal(map[string]any{"before":before,"after_save":afterSave,"after_apply":afterApply,"save_status":saved.Code,"read_status":status.Code,"apply_status":applied.Code,"stale_status":stale.Code,"restarts":restarts})
    fmt.Printf("TRANSACTION:%s\n",output)
}
