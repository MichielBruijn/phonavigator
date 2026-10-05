/*
 * TDxNavLib.dll for Phonavigator on Windows: the 3Dconnexion navigation library interface
 * (NlCreate/NlClose/NlReadValue/NlWriteValue), fed by the Phonavigator tray.
 *
 * The application exposes its camera through property accessors; this library reads the axes
 * from the tray's named pipe and moves the camera in "object mode" around the pivot, like the
 * 3DxWare navigation library. Ported from the TopSo'Linux Wine dll (same author).
 *
 * Pipe: \\.\pipe\phonavigator, a stream of 32-byte events: int32 type (0 = motion),
 * x y z rx ry rz as spacenavd would send them, int32 period in ms.
 *
 * Settings in HKCU\Software\Phonavigator\NavLib (or environment PHONAVIGATOR_NAVLIB_<NAME>):
 * TranslationSpeed, RotationSpeed, AxisSigns and AxisMap, all REG_SZ.
 */

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>

#include <windows.h>

#define PIPE_NAME L"\\\\.\\pipe\\phonavigator"
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif

/* navlib types, see navlib_types.h in the 3Dconnexion SDK */

typedef enum
{
    auto_type = -2, unknown_type = -1, voidptr_type = 0, bool_type, long_type, float_type,
    double_type, point_type, vector_type, matrix_type, string_type, actionnodeexptr_type,
    plane_type, box_type, frustum_type, cstr_type, imagearray_type
} propertyType_t;

typedef struct { double x, y, z; } point_t;
typedef struct { point_t min, max; } box_t;

typedef struct
{
    propertyType_t type;
    union
    {
        void *p;
        UINT32 b;
        LONG l;
        float f;
        double d;
        point_t point;
        box_t box;
        double m[16];
        struct { char *p; SIZE_T length; } string;
    };
} value_t;

typedef LONG (__cdecl *fnGetProperty_t)( UINT64 param, const char *name, value_t *value );
typedef LONG (__cdecl *fnSetProperty_t)( UINT64 param, const char *name, const value_t *value );

typedef struct
{
    const char *name;
    fnGetProperty_t fnGet;
    fnSetProperty_t fnSet;
    UINT64 param;
} accessor_t;

typedef struct
{
    UINT32 size;
    INT8 bMultiThreaded;
    int options;
} nlCreateOptions_t;

#define NL_ROW_MAJOR_ORDER 2

#define NAVLIB_ERROR(x) ((LONG)(((x) & 0xffff) | (4 << 16) | 0x80000000))
#define NAVLIB_EINVAL 22
#define NAVLIB_ENOMEM 12
#define NAVLIB_PROPERTY_NOT_FOUND 0x201

#define WM_NAVLIB_MOTION (WM_APP + 1)
#define MOTION_TIMER 1
#define MOTION_INTERVAL 15   /* ms */
#define MOTION_IDLE 150      /* ms without input before the motion ends */

struct accessor
{
    char *name;
    fnGetProperty_t get;
    fnSetProperty_t set;
    UINT64 param;
};

struct navlib
{
    struct navlib *next;
    UINT64 handle;
    struct accessor *accessors;
    SIZE_T count;
    BOOL row_major;
    BOOL active;
    HWND hwnd;
    BOOL moving;
    BOOL navigating;
    BOOL have_pivot;
    double pivot[3];
    BOOL app_timing;
    double frame_time;
    DWORD last_tick;
};

static struct navlib *navlibs;
static UINT64 next_handle = 1;
static CRITICAL_SECTION cs;

/* latest 3D mouse state, protected by cs */
static int axes[6];
static DWORD axes_tick;
static HANDLE reader_thread;

/* tuning, full deflection of the cap is about 350; defaults as in TopSo'Linux */
static double translation_speed = 2.4 / 350.0;
static double rotation_speed = 3.2 / 350.0;
static int axis_sign[6] = { 1, 1, -1, 1, 1, 1 };
static int axis_map[6] = { 0, 2, 1, 3, 5, 4 };

static struct navlib *find_navlib( UINT64 handle )
{
    struct navlib *nl;
    for (nl = navlibs; nl; nl = nl->next)
        if (nl->handle == handle) return nl;
    return NULL;
}

static struct navlib *active_navlib(void)
{
    struct navlib *nl, *last = NULL;
    for (nl = navlibs; nl; nl = nl->next)
    {
        if (nl->active) return nl;
        last = nl;
    }
    return last;
}

static struct accessor *find_accessor( struct navlib *nl, const char *name )
{
    SIZE_T i;
    for (i = 0; i < nl->count; i++)
        if (!strcmp( nl->accessors[i].name, name )) return &nl->accessors[i];
    return NULL;
}

static BOOL get_value( struct navlib *nl, const char *name, value_t *value )
{
    struct accessor *acc = find_accessor( nl, name );
    LONG ret;

    if (!acc || !acc->get) return FALSE;
    memset( value, 0, sizeof(*value) );
    value->type = auto_type;
    ret = acc->get( acc->param, acc->name, value );
    return !ret;
}

static BOOL set_value( struct navlib *nl, const char *name, const value_t *value )
{
    struct accessor *acc = find_accessor( nl, name );
    LONG ret;

    if (!acc || !acc->set) return FALSE;
    ret = acc->set( acc->param, acc->name, value );
    return !ret;
}

static void set_bool( struct navlib *nl, const char *name, BOOL b )
{
    value_t value = { .type = bool_type };
    value.b = b;
    set_value( nl, name, &value );
}

static void set_long( struct navlib *nl, const char *name, LONG l )
{
    value_t value = { .type = long_type };
    value.l = l;
    set_value( nl, name, &value );
}

/* 3x3 rotation and position of a camera, math convention: world = R * cam + pos */
struct frame
{
    double r[3][3];
    double pos[3];
};

static BOOL translation_in_last_column( struct navlib *nl, const double *m )
{
    /* the bottom row of an affine matrix is (0, 0, 0, 1) */
    if (m[3] == 0.0 && m[7] == 0.0 && m[11] == 0.0) return FALSE;
    if (m[12] == 0.0 && m[13] == 0.0 && m[14] == 0.0) return TRUE;
    return nl->row_major;
}

static void matrix_to_frame( const double *m, BOOL row_major, struct frame *f )
{
    int i, j;
    for (i = 0; i < 3; i++)
    {
        for (j = 0; j < 3; j++) f->r[i][j] = row_major ? m[i * 4 + j] : m[j * 4 + i];
        f->pos[i] = row_major ? m[i * 4 + 3] : m[12 + i];
    }
}

static void frame_to_matrix( const struct frame *f, BOOL row_major, double *m )
{
    int i, j;
    for (i = 0; i < 3; i++)
    {
        for (j = 0; j < 3; j++)
        {
            if (row_major) m[i * 4 + j] = f->r[i][j];
            else m[j * 4 + i] = f->r[i][j];
        }
        if (row_major) m[i * 4 + 3] = f->pos[i];
        else m[12 + i] = f->pos[i];
    }
    if (row_major) m[12] = m[13] = m[14] = 0.0;
    else m[3] = m[7] = m[11] = 0.0;
    m[15] = 1.0;
}

static void axis_angle_matrix( const double axis[3], double angle, double out[3][3] )
{
    double c = cos( angle ), s = sin( angle ), t = 1.0 - c;
    double x = axis[0], y = axis[1], z = axis[2];

    out[0][0] = t * x * x + c;     out[0][1] = t * x * y - s * z; out[0][2] = t * x * z + s * y;
    out[1][0] = t * x * y + s * z; out[1][1] = t * y * y + c;     out[1][2] = t * y * z - s * x;
    out[2][0] = t * x * z - s * y; out[2][1] = t * y * z + s * x; out[2][2] = t * z * z + c;
}

static void mat_mul( double a[3][3], double b[3][3], double out[3][3] )
{
    double tmp[3][3];
    int i, j, k;
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++)
            for (tmp[i][j] = 0.0, k = 0; k < 3; k++) tmp[i][j] += a[i][k] * b[k][j];
    memcpy( out, tmp, sizeof(tmp) );
}

static void mat_vec( double m[3][3], const double v[3], double out[3] )
{
    double tmp[3];
    int i;
    for (i = 0; i < 3; i++) tmp[i] = m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2];
    memcpy( out, tmp, sizeof(tmp) );
}

/* make the columns (camera axes in world space) orthonormal again */
static void orthonormalize( double r[3][3] )
{
    double len, dot;
    int i, c;

    for (c = 0; c < 2; c++)
    {
        if (c == 1)
        {
            for (dot = 0.0, i = 0; i < 3; i++) dot += r[i][0] * r[i][1];
            for (i = 0; i < 3; i++) r[i][1] -= dot * r[i][0];
        }
        for (len = 0.0, i = 0; i < 3; i++) len += r[i][c] * r[i][c];
        if ((len = sqrt( len )) < 1e-12) return;
        for (i = 0; i < 3; i++) r[i][c] /= len;
    }
    r[0][2] = r[1][0] * r[2][1] - r[2][0] * r[1][1];
    r[1][2] = r[2][0] * r[0][1] - r[0][0] * r[2][1];
    r[2][2] = r[0][0] * r[1][1] - r[1][0] * r[0][1];
}

static BOOL get_pivot( struct navlib *nl, double pivot[3] )
{
    value_t value;

    if (get_value( nl, "pivot.position", &value ) && value.type == point_type)
    {
        pivot[0] = value.point.x; pivot[1] = value.point.y; pivot[2] = value.point.z;
        return TRUE;
    }
    if (get_value( nl, "model.extents", &value ) && value.type == box_type &&
        value.box.max.x >= value.box.min.x)
    {
        pivot[0] = (value.box.min.x + value.box.max.x) / 2;
        pivot[1] = (value.box.min.y + value.box.max.y) / 2;
        pivot[2] = (value.box.min.z + value.box.max.z) / 2;
        return TRUE;
    }
    return FALSE;
}

static void navigate( struct navlib *nl, const int in[6], double dt )
{
    double t[3], w[3], pivot[3], rel[3], d[3], rot[3][3], world_rot[3][3], rt[3][3], new_r[3][3];
    double angle, dist = 1.0, extents_scale = 1.0;
    BOOL perspective = TRUE, have_pivot, have_extents = FALSE;
    value_t value, extents;
    struct frame cam;
    BOOL row_major;
    int i, j;

    if (!get_value( nl, "view.affine", &value ) || value.type != matrix_type) return;
    row_major = translation_in_last_column( nl, value.m );
    matrix_to_frame( value.m, row_major, &cam );

    if (get_value( nl, "view.perspective", &extents ) && extents.type == bool_type)
        perspective = extents.b != 0;
    if (!perspective && get_value( nl, "view.extents", &extents ) && extents.type == box_type)
        have_extents = TRUE;

    have_pivot = nl->have_pivot;
    if (have_pivot)
    {
        memcpy( pivot, nl->pivot, sizeof(pivot) );
        for (i = 0; i < 3; i++) rel[i] = cam.pos[i] - pivot[i];
        dist = sqrt( rel[0] * rel[0] + rel[1] * rel[1] + rel[2] * rel[2] );
    }
    if (have_extents) dist = extents.box.max.y - extents.box.min.y;
    if (dist < 1e-9) dist = 1.0;

    /* object mode: the camera moves opposite to the cap, navlib frame is x right, y up, z out */
    for (i = 0; i < 3; i++)
    {
        t[i] = -axis_sign[i] * in[axis_map[i]] * translation_speed * dist * dt;
        w[i] = -axis_sign[i + 3] * in[axis_map[i + 3]] * rotation_speed * dt;
    }

    if (have_extents)
    {
        /* zoom by scaling the orthographic extents instead of moving the camera */
        extents_scale = exp( -t[2] / dist );
        t[2] = 0.0;
    }

    angle = sqrt( w[0] * w[0] + w[1] * w[1] + w[2] * w[2] );
    if (angle > 1e-12 && have_pivot)
    {
        for (i = 0; i < 3; i++) w[i] /= angle;
        axis_angle_matrix( w, angle, rot );
        /* Rounding errors in R would grow every step (R' = R rot R^T R): keep it a rotation,
         * and turn the camera as R' = R rot; the world rotation R rot R^T moves it around the pivot. */
        orthonormalize( cam.r );
        for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) rt[i][j] = cam.r[j][i];
        mat_mul( cam.r, rot, new_r );
        mat_mul( new_r, rt, world_rot );

        mat_vec( world_rot, rel, rel );
        for (i = 0; i < 3; i++) cam.pos[i] = pivot[i] + rel[i];
        memcpy( cam.r, new_r, sizeof(new_r) );
    }

    mat_vec( cam.r, t, d );
    for (i = 0; i < 3; i++) cam.pos[i] += d[i];

    set_long( nl, "transaction", 1 );
    value.type = matrix_type;
    frame_to_matrix( &cam, row_major, value.m );
    set_value( nl, "view.affine", &value );
    if (have_extents && extents_scale != 1.0)
    {
        extents.type = box_type;
        extents.box.min.x *= extents_scale; extents.box.min.y *= extents_scale; extents.box.min.z *= extents_scale;
        extents.box.max.x *= extents_scale; extents.box.max.y *= extents_scale; extents.box.max.z *= extents_scale;
        set_value( nl, "view.extents", &extents );
    }
    set_long( nl, "transaction", 0 );
}

static void load_settings(void);

static LRESULT CALLBACK navlib_wndproc( HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam )
{
    struct navlib *nl;
    int in[6];
    DWORD now, last;
    value_t frame;
    BOOL idle;

    if (msg != WM_NAVLIB_MOTION && msg != WM_TIMER) return DefWindowProcW( hwnd, msg, wparam, lparam );

    EnterCriticalSection( &cs );
    nl = (struct navlib *)GetWindowLongPtrW( hwnd, GWLP_USERDATA );
    memcpy( in, axes, sizeof(in) );
    last = axes_tick;
    LeaveCriticalSection( &cs );
    if (!nl) return 0;

    now = GetTickCount();
    idle = !in[0] && !in[1] && !in[2] && !in[3] && !in[4] && !in[5];

    if (msg == WM_NAVLIB_MOTION)
    {
        if (!nl->moving && !idle)
        {
            load_settings();
            nl->have_pivot = get_pivot( nl, nl->pivot );
            nl->app_timing = get_value( nl, "frame.timingSource", &frame ) && frame.type == long_type && frame.l == 1;
            nl->frame_time = -1.0;
            nl->moving = TRUE;
            nl->last_tick = now;
            set_bool( nl, "motion", TRUE );
            SetTimer( hwnd, MOTION_TIMER, MOTION_INTERVAL, NULL );
        }
        return 0;
    }

    if (!nl->moving) return 0;
    if (idle && now - last > MOTION_IDLE)
    {
        KillTimer( hwnd, MOTION_TIMER );
        nl->moving = FALSE;
        set_bool( nl, "motion", FALSE );
        return 0;
    }
    if (nl->navigating) return 0;
    if (!idle && nl->app_timing && get_value( nl, "frame.time", &frame ) && frame.type == double_type)
    {
        if (frame.d == nl->frame_time) return 0;
        nl->frame_time = frame.d;
    }
    nl->navigating = TRUE;
    if (!idle) navigate( nl, in, min( now - nl->last_tick, 100 ) / 1000.0 );
    nl->navigating = FALSE;
    nl->last_tick = now;
    return 0;
}

static void clear_axes(void)
{
    EnterCriticalSection( &cs );
    memset( axes, 0, sizeof(axes) );
    axes_tick = GetTickCount();
    LeaveCriticalSection( &cs );
}

static DWORD WINAPI reader_proc( void *arg )
{
    INT32 event[8];
    struct navlib *nl;
    HANDLE pipe;
    HWND hwnd;
    DWORD got, done;

    for (;;)
    {
        pipe = CreateFileW( PIPE_NAME, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL );
        if (pipe == INVALID_HANDLE_VALUE)
        {
            Sleep( 1000 );  /* tray not running (yet) */
            continue;
        }
        for (;;)
        {
            for (done = 0; done < sizeof(event); done += got)
                if (!ReadFile( pipe, (char *)event + done, sizeof(event) - done, &got, NULL ) || !got) break;
            if (done < sizeof(event)) break;
            if (event[0] != 0) continue;  /* only motion events so far */

            EnterCriticalSection( &cs );
            memcpy( axes, event + 1, sizeof(axes) );
            axes_tick = GetTickCount();
            hwnd = (nl = active_navlib()) ? nl->hwnd : NULL;
            LeaveCriticalSection( &cs );

            if (hwnd) PostMessageW( hwnd, WM_NAVLIB_MOTION, 0, 0 );
        }
        CloseHandle( pipe );
        clear_axes();  /* tray gone: stop moving */
    }
    return 0;
}

static BOOL get_setting( HKEY key, const char *name, char *buf, DWORD size )
{
    const char *env;
    char var[80] = "PHONAVIGATOR_NAVLIB_";
    int i, n = (int)strlen( var );

    /* environment overrides the registry, e.g. PHONAVIGATOR_NAVLIB_ROTATIONSPEED */
    for (i = 0; name[i] && n + i < (int)sizeof(var) - 1; i++) var[n + i] = toupper( name[i] );
    var[n + i] = 0;
    if ((env = getenv( var )))
    {
        lstrcpynA( buf, env, size );
        return TRUE;
    }
    size--;
    if (!key || RegQueryValueExA( key, name, NULL, NULL, (BYTE *)buf, &size )) return FALSE;
    buf[size] = 0;
    return TRUE;
}

static void parse_list( const char *p, int out[6], BOOL signs )
{
    int i;
    for (i = 0; i < 6 && *p; i++)
    {
        int v = atoi( p );
        if (signs) out[i] = v < 0 ? -1 : 1;
        else if (v >= 0 && v < 6) out[i] = v;
        while (*p && *p != ',') p++;
        if (*p == ',') p++;
    }
}

static void load_settings(void)
{
    static const int default_sign[6] = { 1, 1, -1, 1, 1, 1 };
    static const int default_map[6] = { 0, 2, 1, 3, 5, 4 };
    char buf[64];
    HKEY key = NULL;

    RegOpenKeyExA( HKEY_CURRENT_USER, "Software\\Phonavigator\\NavLib", 0, KEY_READ, &key );
    translation_speed = (get_setting( key, "TranslationSpeed", buf, sizeof(buf) ) ? atof( buf ) : 2.4) / 350.0;
    rotation_speed = (get_setting( key, "RotationSpeed", buf, sizeof(buf) ) ? atof( buf ) : 3.2) / 350.0;
    memcpy( axis_sign, default_sign, sizeof(axis_sign) );
    memcpy( axis_map, default_map, sizeof(axis_map) );
    /* input axis used for each of x y z rx ry rz, e.g. "0,2,1,3,5,4" swaps y and z */
    if (get_setting( key, "AxisMap", buf, sizeof(buf) )) parse_list( buf, axis_map, FALSE );
    if (get_setting( key, "AxisSigns", buf, sizeof(buf) )) parse_list( buf, axis_sign, TRUE );
    if (key) RegCloseKey( key );
}

static HWND create_navlib_window( struct navlib *nl )
{
    static const WCHAR classW[] = L"PhonavigatorNavLib";
    static BOOL registered;
    HWND hwnd;

    if (!registered)
    {
        WNDCLASSW wc = { .lpfnWndProc = navlib_wndproc, .lpszClassName = classW };
        RegisterClassW( &wc );
        registered = TRUE;
    }
    if ((hwnd = CreateWindowW( classW, NULL, 0, 0, 0, 0, 0, HWND_MESSAGE, NULL, NULL, NULL )))
        SetWindowLongPtrW( hwnd, GWLP_USERDATA, (LONG_PTR)nl );
    return hwnd;
}

LONG __cdecl NlCreate( UINT64 *handle, const char *appname, const accessor_t *accessors,
                       SIZE_T count, const nlCreateOptions_t *options )
{
    struct navlib *nl, **tail;
    SIZE_T i;

    if (!handle || (count && !accessors)) return NAVLIB_ERROR( NAVLIB_EINVAL );
    if (!(nl = calloc( 1, sizeof(*nl) ))) return NAVLIB_ERROR( NAVLIB_ENOMEM );
    if (count && !(nl->accessors = calloc( count, sizeof(*nl->accessors) )))
    {
        free( nl );
        return NAVLIB_ERROR( NAVLIB_ENOMEM );
    }
    for (i = 0; i < count; i++)
    {
        /* the names may be marshalled temporaries, keep our own copy */
        nl->accessors[i].name = strdup( accessors[i].name ? accessors[i].name : "" );
        nl->accessors[i].get = accessors[i].fnGet;
        nl->accessors[i].set = accessors[i].fnSet;
        nl->accessors[i].param = accessors[i].param;
    }
    nl->count = count;
    if (options && options->size >= sizeof(*options)) nl->row_major = !!(options->options & NL_ROW_MAJOR_ORDER);
    nl->hwnd = create_navlib_window( nl );

    EnterCriticalSection( &cs );
    nl->handle = next_handle++;
    for (tail = &navlibs; *tail; tail = &(*tail)->next) ;
    *tail = nl;
    if (!reader_thread) reader_thread = CreateThread( NULL, 0, reader_proc, NULL, 0, NULL );
    LeaveCriticalSection( &cs );

    *handle = nl->handle;
    return 0;
}

LONG __cdecl NlClose( UINT64 handle )
{
    struct navlib *nl = NULL, **link;
    SIZE_T i;

    EnterCriticalSection( &cs );
    for (link = &navlibs; *link; link = &(*link)->next)
        if ((*link)->handle == handle)
        {
            nl = *link;
            *link = nl->next;
            break;
        }
    LeaveCriticalSection( &cs );
    if (!nl) return NAVLIB_ERROR( NAVLIB_EINVAL );

    if (nl->hwnd)
    {
        SetWindowLongPtrW( nl->hwnd, GWLP_USERDATA, 0 );
        DestroyWindow( nl->hwnd );
    }
    for (i = 0; i < nl->count; i++) free( nl->accessors[i].name );
    free( nl->accessors );
    free( nl );
    return 0;
}

LONG __cdecl NlReadValue( UINT64 handle, const char *name, value_t *value )
{
    struct navlib *nl;
    LONG ret = NAVLIB_ERROR( NAVLIB_PROPERTY_NOT_FOUND );

    if (!name || !value) return NAVLIB_ERROR( NAVLIB_EINVAL );
    EnterCriticalSection( &cs );
    if (!(nl = find_navlib( handle ))) ret = NAVLIB_ERROR( NAVLIB_EINVAL );
    else if (!strcmp( name, "active" ))
    {
        value->type = bool_type;
        value->b = nl->active;
        ret = 0;
    }
    else if (!strcmp( name, "motion" ))
    {
        value->type = bool_type;
        value->b = nl->moving;
        ret = 0;
    }
    else if (!strcmp( name, "device.present" ))
    {
        value->type = bool_type;
        value->b = TRUE;
        ret = 0;
    }
    else if (!strcmp( name, "settings.changed" ))
    {
        value->type = long_type;
        value->l = 0;
        ret = 0;
    }
    LeaveCriticalSection( &cs );
    return ret;
}

LONG __cdecl NlWriteValue( UINT64 handle, const char *name, const value_t *value )
{
    struct navlib *nl, *other;

    if (!name || !value) return NAVLIB_ERROR( NAVLIB_EINVAL );
    EnterCriticalSection( &cs );
    if (!(nl = find_navlib( handle )))
    {
        LeaveCriticalSection( &cs );
        return NAVLIB_ERROR( NAVLIB_EINVAL );
    }
    if ((!strcmp( name, "active" ) || !strcmp( name, "focus" )) && value->type == bool_type && value->b)
    {
        for (other = navlibs; other; other = other->next) other->active = FALSE;
        nl->active = TRUE;
    }
    else if (!strcmp( name, "active" ) && value->type == bool_type) nl->active = FALSE;
    LeaveCriticalSection( &cs );

    /* commands, images and settings are accepted but not used */
    return 0;
}

BOOL WINAPI DllMain( HINSTANCE instance, DWORD reason, void *reserved )
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls( instance );
        InitializeCriticalSection( &cs );
    }
    return TRUE;
}
