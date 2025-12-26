#!/usr/bin/env python3

"""
Grant Viewer Access to All GCP Projects
This script grants read-only IAM permissions to a user/service account across all projects.
Runs in parallel for efficiency with thousands of projects.
"""

import argparse
import concurrent.futures
import sys
import time
import threading

try:
    import googleapiclient.discovery
    import google.auth
except ImportError:
    print("\nERROR: Missing required GCP SDK packages. Run the following command to install/upgrade:\n")
    print("pip3 install --upgrade google-api-python-client")
    sys.exit(1)


####
# Configuration
####

DEFAULT_MAX_WORKERS = 100
DEFAULT_ROLE = "roles/viewer"  # Read-only access to all resources

parser = argparse.ArgumentParser(
    description='Grant read-only IAM permissions to scan GCP Projects',
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  # Grant Viewer role to current authenticated user on all projects
  python3 grant-viewer-access.py --all
  
  # Grant to specific user
  python3 grant-viewer-access.py --all --user user@example.com
  
  # Grant to service account
  python3 grant-viewer-access.py --all --service-account sa@project.iam.gserviceaccount.com
  
  # Use custom role with specific permissions
  python3 grant-viewer-access.py --all --role roles/browser
  
  # Higher parallelism for faster execution
  python3 grant-viewer-access.py --all --max-workers 300

Recommended Roles:
  roles/viewer              - Full read access (recommended for inventory)
  roles/browser             - Minimal read access (project metadata only)
  Custom role               - Create with specific permissions needed
    """
)

parser.add_argument(
    '--all',
    action='store_true',
    dest='all_projects',
    help='Process all GCP Projects',
    default=False
)
parser.add_argument(
    '--projects',
    dest='projects_file',
    help='Process projects listed in specified file (one ID per line)',
    default='projects.txt'
)
parser.add_argument(
    '--user',
    dest='user_email',
    help='User email to grant access to (e.g., user@example.com)',
    default=None
)
parser.add_argument(
    '--service-account',
    dest='service_account',
    help='Service account to grant access to (e.g., sa@project.iam.gserviceaccount.com)',
    default=None
)
parser.add_argument(
    '--role',
    dest='role',
    help=f'IAM role to grant (default: {DEFAULT_ROLE})',
    default=DEFAULT_ROLE
)
parser.add_argument(
    '--max-workers',
    dest='max_workers',
    help=f'Maximum parallel requests (default: {DEFAULT_MAX_WORKERS})',
    type=int,
    default=DEFAULT_MAX_WORKERS
)
parser.add_argument(
    '--dry-run',
    action='store_true',
    dest='dry_run',
    help='Show what would be done without making changes',
    default=False
)
parser.add_argument(
    '--check-only',
    action='store_true',
    dest='check_only',
    help='Only check current permissions, do not grant access',
    default=False
)

args = parser.parse_args()

if args.max_workers < 1 or args.max_workers > 1000:
    print(f"ERROR: --max-workers {args.max_workers} out of range: [1 .. 1000]")
    sys.exit(1)

####
# Global State
####

try:
    google_auth_credential, project_id = google.auth.default()
except Exception as ex:
    print(f"ERROR: Unable to get default credentials: {ex}")
    print("\nMake sure you're authenticated:")
    print("  gcloud auth application-default login")
    sys.exit(1)

google_api_config = {
    'credentials': google_auth_credential,
    'num_retries': 3,
    'static_discovery': True
}

# Thread-safe counters
success_count = 0
failure_count = 0
already_granted_count = 0
counter_lock = threading.Lock()

errors_log = []
errors_lock = threading.Lock()


####
# Helper Functions
####

def get_current_user_email():
    """Get the email of the currently authenticated user"""
    try:
        # Try to get from service account
        if hasattr(google_auth_credential, 'service_account_email'):
            return f"serviceAccount:{google_auth_credential.service_account_email}"
        
        # Try to get user email from auth
        if hasattr(google_auth_credential, '_service_account_email'):
            return f"serviceAccount:{google_auth_credential._service_account_email}"
        
        # For user accounts, we need to query
        service = googleapiclient.discovery.build('oauth2', 'v2', **google_api_config)
        user_info = service.userinfo().get().execute()
        email = user_info.get('email')
        service.close()
        
        if email:
            return f"user:{email}"
        
        return None
    except Exception as ex:
        print(f"Warning: Could not determine current user email: {ex}")
        return None


def get_member_identifier():
    """Get the member identifier to grant permissions to"""
    if args.user_email:
        return f"user:{args.user_email}"
    elif args.service_account:
        return f"serviceAccount:{args.service_account}"
    else:
        # Use current authenticated user
        member = get_current_user_email()
        if not member:
            print("\nERROR: Could not determine current user.")
            print("Please specify --user or --service-account explicitly.")
            sys.exit(1)
        return member


def log_error(message):
    """Thread-safe error logging"""
    with errors_lock:
        errors_log.append(message)


def get_all_projects():
    """Get all active GCP projects"""
    projects = []
    try:
        client = googleapiclient.discovery.build('cloudresourcemanager', 'v1', **google_api_config)
        request = client.projects().list()
        
        while request is not None:
            response = request.execute()
            if 'projects' in response:
                for project in response['projects']:
                    if project['lifecycleState'] == 'ACTIVE':
                        projects.append(project['projectId'])
            
            if 'nextPageToken' in response:
                request = client.projects().list_next(previous_request=request, previous_response=response)
            else:
                request = None
        
        client.close()
    except Exception as ex:
        print(f"ERROR: Failed to list projects: {ex}")
        sys.exit(1)
    
    return sorted(projects)


def get_projects_from_file(filename):
    """Get project IDs from file"""
    projects = []
    try:
        with open(filename, 'r') as f:
            for line in f:
                project_id = line.strip()
                if project_id:
                    projects.append(project_id)
    except FileNotFoundError:
        print(f"ERROR: File not found: {filename}")
        sys.exit(1)
    
    return projects


def check_permission(project_id, member, role):
    """Check if member already has the role on the project"""
    try:
        client = googleapiclient.discovery.build('cloudresourcemanager', 'v1', **google_api_config)
        policy = client.projects().getIamPolicy(
            resource=project_id,
            body={}
        ).execute()
        client.close()
        
        # Check if member already has this role
        for binding in policy.get('bindings', []):
            if binding['role'] == role:
                if member in binding.get('members', []):
                    return True
        
        return False
    except Exception as ex:
        log_error(f"Project: {project_id} - Failed to check permissions: {ex}")
        return None


def grant_iam_permission(project_id, member, role):
    """Grant IAM role to member on project"""
    global success_count, failure_count, already_granted_count
    
    try:
        # Check current permissions
        has_permission = check_permission(project_id, member, role)
        
        if has_permission is True:
            with counter_lock:
                already_granted_count += 1
            return {
                'project_id': project_id,
                'status': 'already_granted',
                'message': f'Already has {role}'
            }
        
        if args.dry_run or args.check_only:
            return {
                'project_id': project_id,
                'status': 'would_grant',
                'message': f'Would grant {role}'
            }
        
        # Grant the permission
        client = googleapiclient.discovery.build('cloudresourcemanager', 'v1', **google_api_config)
        
        # Get current policy
        policy = client.projects().getIamPolicy(
            resource=project_id,
            body={}
        ).execute()
        
        # Add the binding
        binding_found = False
        for binding in policy.get('bindings', []):
            if binding['role'] == role:
                if member not in binding.get('members', []):
                    binding['members'].append(member)
                binding_found = True
                break
        
        if not binding_found:
            policy.setdefault('bindings', []).append({
                'role': role,
                'members': [member]
            })
        
        # Set the updated policy
        client.projects().setIamPolicy(
            resource=project_id,
            body={'policy': policy}
        ).execute()
        
        client.close()
        
        with counter_lock:
            success_count += 1
        
        return {
            'project_id': project_id,
            'status': 'granted',
            'message': f'Successfully granted {role}'
        }
        
    except Exception as ex:
        with counter_lock:
            failure_count += 1
        
        error_msg = f"Project: {project_id} - {str(ex)}"
        log_error(error_msg)
        
        return {
            'project_id': project_id,
            'status': 'failed',
            'message': str(ex)
        }


def process_projects_parallel(projects, member, role):
    """Process all projects in parallel"""
    print(f"\nProcessing {len(projects)} projects with {args.max_workers} workers...")
    print(f"Member: {member}")
    print(f"Role: {role}")
    
    if args.dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")
    elif args.check_only:
        print("\n*** CHECK ONLY MODE - No changes will be made ***\n")
    
    start_time = time.time()
    completed = 0
    progress_lock = threading.Lock()
    
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        for project_id in projects:
            future = executor.submit(grant_iam_permission, project_id, member, role)
            futures[future] = project_id
        
        for future in concurrent.futures.as_completed(futures):
            project_id = futures[future]
            result = future.result()
            
            with progress_lock:
                completed += 1
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                remaining = (len(projects) - completed) / rate if rate > 0 else 0
                
                status_icon = {
                    'granted': '✓',
                    'already_granted': '→',
                    'would_grant': '?',
                    'failed': '✗'
                }.get(result['status'], '?')
                
                if completed % 10 == 0 or result['status'] in ['granted', 'failed']:
                    print(f"{status_icon} [{completed}/{len(projects)}] {result['project_id']}: {result['message']} | "
                          f"Rate: {rate:.1f}/sec | ETA: {remaining/60:.1f} min")
    
    elapsed = time.time() - start_time
    
    print(f"\n{'='*80}")
    print(f"Completed in {elapsed/60:.1f} minutes ({len(projects)/elapsed:.1f} projects/sec)")
    print(f"{'='*80}")
    print(f"  ✓ Successfully granted: {success_count}")
    print(f"  → Already had access: {already_granted_count}")
    print(f"  ✗ Failed: {failure_count}")
    print(f"{'='*80}\n")
    
    if errors_log:
        print(f"\n{len(errors_log)} errors occurred. First 10:")
        for error in errors_log[:10]:
            print(f"  - {error}")
        
        # Write all errors to file
        with open('grant-access-errors.log', 'w') as f:
            for error in errors_log:
                f.write(error + '\n')
        print(f"\nFull error log written to: grant-access-errors.log")


def main():
    """Main function"""
    print("=" * 80)
    print("GCP IAM Permission Grant Script")
    print("=" * 80)
    
    # Get member to grant permissions to
    member = get_member_identifier()
    print(f"\nGranting permissions to: {member}")
    print(f"Role: {args.role}")
    
    # Get projects
    if args.all_projects:
        print("\nFetching all active projects...")
        projects = get_all_projects()
        print(f"Found {len(projects)} active projects")
    else:
        print(f"\nReading projects from: {args.projects_file}")
        projects = get_projects_from_file(args.projects_file)
        print(f"Found {len(projects)} projects")
    
    if not projects:
        print("ERROR: No projects to process")
        sys.exit(1)
    
    # Confirm before proceeding
    if not args.dry_run and not args.check_only:
        print(f"\n{'!'*80}")
        print(f"WARNING: About to modify IAM policies on {len(projects)} projects")
        print(f"{'!'*80}")
        response = input("\nType 'yes' to continue: ")
        if response.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    
    # Process projects
    process_projects_parallel(projects, member, args.role)
    
    print("\nDone! You can now run the resource inventory script.")


if __name__ == '__main__':
    main()

