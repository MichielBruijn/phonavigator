/* Smoke test: load a TDxNavLib.dll, expose a camera and print how the tray moves it.
 * Usage: navlib_test <path to TDxNavLib.dll> [seconds] */
#include <stdio.h>
#include <string.h>
#include <windows.h>

typedef struct { double x, y, z; } point_t;
typedef struct { int type; union { void *p; UINT32 b; LONG l; float f; double d; point_t point; double m[16]; }; } value_t;
typedef LONG (__cdecl *get_t)( UINT64, const char *, value_t * );
typedef LONG (__cdecl *set_t)( UINT64, const char *, const value_t * );
typedef struct { const char *name; get_t get; set_t set; UINT64 param; } accessor_t;
typedef struct { UINT32 size; INT8 mt; int options; } options_t;
typedef LONG (__cdecl *create_t)( UINT64 *, const char *, const accessor_t *, SIZE_T, const options_t * );
typedef LONG (__cdecl *write_t)( UINT64, const char *, const value_t * );

static double view[16] = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,10,1 };  /* column major, camera at z 10 */
static int sets;

static LONG __cdecl get_affine( UINT64 p, const char *n, value_t *v ) { v->type = 7; memcpy( v->m, view, sizeof(view) ); return 0; }
static LONG __cdecl set_affine( UINT64 p, const char *n, const value_t *v )
{
    memcpy( view, v->m, sizeof(view) );
    sets++;
    if (getenv( "NAVLIB_TEST_VERBOSE" ))
        printf( "%3d %lu: pos %.3f %.3f %.3f  r00 %.4f\n", sets, GetTickCount() % 100000, view[12], view[13], view[14], view[0] );
    return 0;
}
static LONG __cdecl get_pivot( UINT64 p, const char *n, value_t *v ) { v->type = 5; v->point.x = v->point.y = v->point.z = 0; return 0; }
static LONG __cdecl get_persp( UINT64 p, const char *n, value_t *v ) { v->type = 1; v->b = 1; return 0; }
static LONG __cdecl set_any( UINT64 p, const char *n, const value_t *v ) { return 0; }

/* stand-in for the tray: rotate about the spacenavd y axis */
static DWORD WINAPI feed( void *arg )
{
    INT32 ev[8] = { 0, 0, 0, 0, 0, 150, 0, 16 };
    HANDLE pipe = CreateNamedPipeW( L"\\\\.\\pipe\\phonavigator", PIPE_ACCESS_OUTBOUND, PIPE_TYPE_BYTE, 1, 4096, 0, 0, NULL );
    DWORD done;
    int i;
    ConnectNamedPipe( pipe, NULL );
    for (i = 0; i < 100; i++) { WriteFile( pipe, ev, sizeof(ev), &done, NULL ); ev[5] ^= 1; Sleep( 16 ); }
    ev[5] = 0;
    WriteFile( pipe, ev, sizeof(ev), &done, NULL );
    return 0;
}

int main( int argc, char **argv )
{
    accessor_t acc[] = {
        { "view.affine", get_affine, set_affine, 0 }, { "pivot.position", get_pivot, NULL, 0 },
        { "view.perspective", get_persp, NULL, 0 }, { "motion", NULL, set_any, 0 }, { "transaction", NULL, set_any, 0 },
    };
    options_t opt = { sizeof(opt), 0, 0 };
    value_t on = { .type = 1, .b = 1 };
    HMODULE lib = LoadLibraryA( argc > 1 ? argv[1] : "TDxNavLib.dll" );
    DWORD end = GetTickCount() + 1000 * (argc > 2 ? atoi( argv[2] ) : 5);
    UINT64 h;
    MSG msg;

    if (getenv( "NAVLIB_TEST_FEED" )) CreateThread( NULL, 0, feed, NULL, 0, NULL );
    if (!lib) { printf( "cannot load the dll (%lu)\n", GetLastError() ); return 1; }
    if (((create_t)GetProcAddress( lib, "NlCreate" ))( &h, "test", acc, 5, &opt )) { printf( "NlCreate failed\n" ); return 1; }
    ((write_t)GetProcAddress( lib, "NlWriteValue" ))( h, "active", &on );
    while (GetTickCount() < end)
    {
        while (PeekMessageW( &msg, NULL, 0, 0, PM_REMOVE )) DispatchMessageW( &msg );
        Sleep( 5 );
    }
    printf( "camera updates: %d\nposition: %.3f %.3f %.3f\n", sets, view[12], view[13], view[14] );
    return 0;
}
