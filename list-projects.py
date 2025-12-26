#!/usr/bin/env python3

"""
List and Filter GCP Projects
Simple script to list all GCP projects and optionally filter them by pattern.
"""

import argparse
import re
import sys

try:
    import googleapiclient.discovery
    import google.auth
except ImportError:
    print("\nERROR: Missing required GCP SDK packages. Run the following command to install/upgrade:\n")
    print("pip3 install --upgrade google-api-python-client")
    sys.exit(1)


parser = argparse.ArgumentParser(
    description='List and filter GCP Projects',
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  # List all projects to file
  python3 list-projects.py --output projects-all.txt
  
  # List and exclude test/dev projects
  python3 list-projects.py --output projects-prod.txt --exclude "test|dev|demo|sandbox"
  
  # List only specific pattern
  python3 list-projects.py --output projects-prod.txt --include "prod|production"
  
  # List with statistics
  python3 list-projects.py --output projects.txt --stats
    """
)

parser.add_argument(
    '--output',
    dest='output_file',
    help='Output file to save project IDs (default: projects.txt)',
    default='projects.txt'
)
parser.add_argument(
    '--exclude',
    dest='exclude_pattern',
    help='Regex pattern to exclude projects (e.g., "test|dev|demo")',
    default=None
)
parser.add_argument(
    '--include',
    dest='include_pattern',
    help='Regex pattern to include only matching projects (e.g., "prod|production")',
    default=None
)
parser.add_argument(
    '--stats',
    action='store_true',
    dest='show_stats',
    help='Show statistics about filtered projects',
    default=False
)
parser.add_argument(
    '--preview',
    action='store_true',
    dest='preview_only',
    help='Preview filtered projects without writing to file',
    default=False
)

args = parser.parse_args()


def get_all_projects():
    """Get all active GCP projects"""
    projects = []
    
    try:
        google_auth_credential, _ = google.auth.default()
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
    
    print("Fetching all GCP projects...")
    
    try:
        client = googleapiclient.discovery.build('cloudresourcemanager', 'v1', **google_api_config)
        request = client.projects().list()
        
        while request is not None:
            response = request.execute()
            if 'projects' in response:
                for project in response['projects']:
                    if project['lifecycleState'] == 'ACTIVE':
                        project_id = project['projectId']
                        project_name = project.get('name', 'UNNAMED')
                        projects.append({
                            'id': project_id,
                            'name': project_name,
                            'number': project.get('projectNumber', 'N/A')
                        })
            
            if 'nextPageToken' in response:
                request = client.projects().list_next(previous_request=request, previous_response=response)
            else:
                request = None
        
        client.close()
    except Exception as ex:
        print(f"ERROR: Failed to list projects: {ex}")
        sys.exit(1)
    
    return sorted(projects, key=lambda p: p['id'])


def filter_projects(projects, include_pattern=None, exclude_pattern=None):
    """Filter projects by regex patterns"""
    filtered = []
    excluded = []
    
    for project in projects:
        project_id = project['id']
        
        # Check exclude pattern
        if exclude_pattern:
            if re.search(exclude_pattern, project_id, re.IGNORECASE):
                excluded.append(project)
                continue
        
        # Check include pattern
        if include_pattern:
            if not re.search(include_pattern, project_id, re.IGNORECASE):
                excluded.append(project)
                continue
        
        filtered.append(project)
    
    return filtered, excluded


def show_statistics(all_projects, filtered_projects, excluded_projects):
    """Show filtering statistics"""
    print("\n" + "="*80)
    print("PROJECT FILTERING STATISTICS")
    print("="*80)
    print(f"Total projects found:      {len(all_projects)}")
    print(f"Projects after filtering:  {len(filtered_projects)}")
    print(f"Projects excluded:         {len(excluded_projects)}")
    print(f"Reduction:                 {len(excluded_projects)/len(all_projects)*100:.1f}%")
    
    if excluded_projects:
        print(f"\n--- Sample Excluded Projects (first 20) ---")
        for i, proj in enumerate(excluded_projects[:20], 1):
            print(f"  {i:3d}. {proj['id']}")
        if len(excluded_projects) > 20:
            print(f"  ... and {len(excluded_projects) - 20} more")
    
    print("\n" + "="*80)


def main():
    """Main function"""
    print("="*80)
    print("GCP Project List & Filter Tool")
    print("="*80)
    
    # Get all projects
    all_projects = get_all_projects()
    print(f"Found {len(all_projects)} active projects")
    
    # Filter projects
    filtered_projects, excluded_projects = filter_projects(
        all_projects,
        include_pattern=args.include_pattern,
        exclude_pattern=args.exclude_pattern
    )
    
    print(f"After filtering: {len(filtered_projects)} projects")
    
    # Show statistics if requested
    if args.show_stats:
        show_statistics(all_projects, filtered_projects, excluded_projects)
    
    # Preview mode
    if args.preview_only:
        print("\n--- Preview of Filtered Projects (first 50) ---")
        for i, proj in enumerate(filtered_projects[:50], 1):
            print(f"  {i:4d}. {proj['id']:60s} [{proj['name']}]")
        if len(filtered_projects) > 50:
            print(f"  ... and {len(filtered_projects) - 50} more")
        print("\nPreview only - no file written.")
        return
    
    # Write to file
    try:
        with open(args.output_file, 'w') as f:
            for proj in filtered_projects:
                f.write(proj['id'] + '\n')
        
        print(f"\n✓ {len(filtered_projects)} project IDs written to: {args.output_file}")
        
        # Also create a detailed file
        detailed_file = args.output_file.replace('.txt', '-detailed.txt')
        with open(detailed_file, 'w') as f:
            f.write("PROJECT_ID,PROJECT_NAME,PROJECT_NUMBER\n")
            for proj in filtered_projects:
                f.write(f"{proj['id']},{proj['name']},{proj['number']}\n")
        
        print(f"✓ Detailed info written to: {detailed_file}")
        
    except Exception as ex:
        print(f"ERROR: Failed to write to file: {ex}")
        sys.exit(1)
    
    print("\n--- Next Steps ---")
    print(f"1. Review the filtered list: cat {args.output_file}")
    print(f"2. Edit manually if needed: nano {args.output_file}")
    print(f"3. Use with inventory script:")
    print(f"   python3 resource-count-gcp-v2.py --projects --max-workers 300")
    print("="*80)


if __name__ == '__main__':
    main()

